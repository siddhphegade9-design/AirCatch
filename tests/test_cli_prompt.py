"""The keyboard prompt must survive stray keystrokes and timeouts.

The tests wait for the question to be on screen before 'typing', exactly like a person would,
so they do not depend on how fast the machine is.
"""
import asyncio
import io
import os
import sys

import pytest

from aircatch.transfer.cli import LinePrompter


@pytest.fixture
def keyboard(monkeypatch):
    """A fake keyboard: call type_('text') and the prompter reads it as if typed."""
    read_fd, write_fd = os.pipe()
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(os.fdopen(read_fd, "rb")))
    writer = os.fdopen(write_fd, "w")

    def type_(text):
        writer.write(text)
        writer.flush()

    yield type_
    writer.close()          # lets the reader thread see 'keyboard closed' and finish


async def until(condition, timeout=10.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not condition():
        assert loop.time() < deadline, "timed out waiting for the program"
        await asyncio.sleep(0.02)


async def question_shown(prompter, number):
    await until(lambda: prompter.prompts_shown >= number)


async def delivered(prompter):
    """Wait until typed text has reached the prompter's queue."""
    await until(lambda: prompter._queue is not None and not prompter._queue.empty())


def test_yes_and_no_answers(keyboard):
    async def scenario():
        prompter = LinePrompter()
        for number, (typed, expected) in enumerate(
                (("y\n", True), ("YES\n", True), ("n\n", False), ("no\n", False)), start=1):
            task = asyncio.create_task(prompter.ask("Accept? "))
            await question_shown(prompter, number)
            keyboard(typed)
            assert await asyncio.wait_for(task, 5) is expected
    asyncio.run(scenario())


def test_stray_enter_before_the_first_question_does_not_decline_it(keyboard):
    """The real-world bug: Enter pressed while the receiver was idle declined the next offer instantly."""
    async def scenario():
        keyboard("\n")                                          # user pressed Enter earlier
        prompter = LinePrompter()
        task = asyncio.create_task(prompter.ask("Accept? "))
        await question_shown(prompter, 1)
        await asyncio.sleep(0.3)
        assert not task.done()                                   # still waiting for a real answer
        keyboard("y\n")
        assert await asyncio.wait_for(task, 5) is True
    asyncio.run(scenario())


def test_enter_while_waiting_is_ignored(keyboard):
    async def scenario():
        prompter = LinePrompter()
        task = asyncio.create_task(prompter.ask("Accept? "))
        await question_shown(prompter, 1)
        keyboard("\n\n   \n")
        await asyncio.sleep(0.3)
        assert not task.done()
        keyboard("y\n")
        assert await asyncio.wait_for(task, 5) is True
    asyncio.run(scenario())


def test_something_else_is_not_an_answer(keyboard):
    async def scenario():
        prompter = LinePrompter()
        task = asyncio.create_task(prompter.ask("Accept? "))
        await question_shown(prompter, 1)
        keyboard("maybe\n")
        await asyncio.sleep(0.3)
        assert not task.done()
        keyboard("n\n")
        assert await asyncio.wait_for(task, 5) is False
    asyncio.run(scenario())


def test_timed_out_question_does_not_steal_the_next_answer(keyboard):
    async def scenario():
        prompter = LinePrompter()
        first = asyncio.create_task(prompter.ask("Accept? "))
        await question_shown(prompter, 1)
        with pytest.raises(asyncio.TimeoutError):               # nobody answers in time
            await asyncio.wait_for(asyncio.shield(first), 0.3)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        task = asyncio.create_task(prompter.ask("Accept? "))     # the next offer
        await question_shown(prompter, 2)
        keyboard("y\n")
        assert await asyncio.wait_for(task, 5) is True           # and the answer reaches it
    asyncio.run(scenario())


def test_old_keystrokes_never_answer_a_new_question(keyboard):
    async def scenario():
        prompter = LinePrompter()
        first = asyncio.create_task(prompter.ask("Accept? "))
        await question_shown(prompter, 1)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        keyboard("y\n")                                         # typed too late, no question open
        await delivered(prompter)                                # it has reached the program
        task = asyncio.create_task(prompter.ask("Accept? "))
        await question_shown(prompter, 2)
        keyboard("n\n")
        assert await asyncio.wait_for(task, 5) is False          # the stale 'y' was discarded
    asyncio.run(scenario())
