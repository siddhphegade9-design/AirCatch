import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts
import QtQuick.Shapes

Window {
    id: root
    width: 1180
    height: 780
    minimumWidth: 980
    minimumHeight: 720
    visible: true
    title: backend.deviceName ? "AirCatch — " + backend.deviceName : "AirCatch"
    color: "#070b16"

    // ---- palette and helpers -------------------------------------------------------------
    readonly property color cardColor: "#0d1426"
    readonly property color lineColor: "#1c2a4c"
    readonly property color textColor: "#e8f1ff"
    readonly property color mutedColor: "#7f8fb8"
    readonly property color cyan: "#22d3ee"
    readonly property color green: "#34d399"
    readonly property color red: "#fb7185"
    readonly property color amber: "#fbbf24"
    function d(ms) { return backend.reducedMotion ? 1 : ms }          // animation length
    function kindColor(kind) {
        var map = { image: "#38bdf8", pdf: "#fb7185", video: "#a78bfa", audio: "#f472b6",
                    document: "#60a5fa", sheet: "#34d399", slides: "#fbbf24", archive: "#94a3b8" }
        return map[kind] || "#64748b"
    }

    // ---- reusable pieces -----------------------------------------------------------------
    component Btn: Rectangle {
        property string label: ""
        property bool primary: true
        property bool active: true
        signal clicked()
        implicitWidth: lbl.implicitWidth + 34
        implicitHeight: 38
        radius: height / 2
        opacity: active ? 1 : 0.45
        color: !primary ? (ma.containsMouse && active ? "#1b2c52" : "#13203f")
                        : (ma.containsMouse && active ? "#4fe0f5" : "#22c7e6")
        border.color: primary ? "transparent" : "#2a3d6e"
        Behavior on color { ColorAnimation { duration: 120 } }
        Text {
            id: lbl
            anchors.centerIn: parent
            width: Math.min(implicitWidth, parent.width - 24)      // long labels (a long PC name) are cut with …
            elide: Text.ElideRight
            horizontalAlignment: Text.AlignHCenter
            text: parent.label
            color: parent.primary ? "#04101c" : "#d4e4ff"
            font.pixelSize: 13
            font.weight: Font.DemiBold
        }
        MouseArea {
            id: ma
            anchors.fill: parent
            hoverEnabled: true
            enabled: parent.active
            cursorShape: Qt.PointingHandCursor
            onClicked: parent.clicked()
        }
    }

    component Card: Rectangle {
        property string title: ""
        default property alias content: body.data
        color: root.cardColor
        radius: 18
        border.color: root.lineColor
        Text {
            id: heading
            x: 18; y: 14
            text: parent.title
            color: root.textColor
            font.pixelSize: 12
            font.weight: Font.Bold
            font.letterSpacing: 1.4
            visible: parent.title !== ""
        }
        Item {
            id: body
            anchors.fill: parent
            anchors.topMargin: heading.visible ? 42 : 0
        }
    }

    component FileThumb: Rectangle {
        property bool hasImage: false
        property string source: ""
        property string badge: ""
        property string kind: "other"
        radius: 16
        color: "#0f1932"
        border.color: "#2b4170"
        Image {
            anchors.fill: parent
            anchors.margins: 8
            visible: parent.hasImage
            source: parent.hasImage ? parent.source : ""
            fillMode: Image.PreserveAspectFit
            cache: false
        }
        Rectangle {
            visible: !parent.hasImage
            anchors.centerIn: parent
            width: Math.min(parent.width, parent.height) * 0.46
            height: width * 1.22
            radius: 10
            color: root.kindColor(parent.kind)
            Text {
                anchors.centerIn: parent
                text: parent.parent.badge
                color: "#06101f"
                font.pixelSize: Math.max(12, parent.width * 0.26)
                font.weight: Font.Black
            }
        }
    }

    // ---- background ----------------------------------------------------------------------
    Rectangle {
        anchors.fill: parent
        gradient: Gradient {
            GradientStop { position: 0.0; color: "#080d1c" }
            GradientStop { position: 1.0; color: "#0b1226" }
        }
    }
    Rectangle {      // soft idle glow
        id: glow
        width: 520; height: 520; radius: 260
        x: parent.width - 360; y: -200
        color: "#0e2a46"
        opacity: 0.55
        SequentialAnimation on opacity {
            running: !backend.reducedMotion
            loops: Animation.Infinite
            NumberAnimation { to: 0.8; duration: 3200; easing.type: Easing.InOutSine }
            NumberAnimation { to: 0.5; duration: 3200; easing.type: Easing.InOutSine }
        }
    }

    DropArea {
        anchors.fill: parent
        onDropped: function (drop) {
            if (drop.hasUrls && drop.urls.length > 0) backend.chooseFile(drop.urls[0].toString())
        }
    }
    FileDialog {
        id: fileDialog
        title: "Choose a file to send"
        onAccepted: backend.chooseFile(selectedFile.toString())
    }

    // ---- main layout ---------------------------------------------------------------------
    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 24
        spacing: 16

        RowLayout {
            Layout.fillWidth: true
            spacing: 12
            Image {
                source: "assets/aircatch-logo.png"
                Layout.preferredHeight: 44
                Layout.preferredWidth: 44 * 900 / 259
                fillMode: Image.PreserveAspectFit
                smooth: true
            }
            Item { Layout.fillWidth: true }
            Rectangle {
                Layout.preferredHeight: 36
                Layout.preferredWidth: Math.min(statusText.implicitWidth + 44, 460)
                radius: 18
                color: "#0f1a33"
                border.color: backend.networkReady ? "#1f5a54" : "#5a4a1f"
                Rectangle {
                    x: 14; anchors.verticalCenter: parent.verticalCenter
                    width: 9; height: 9; radius: 5
                    color: backend.networkReady ? root.green : root.amber
                }
                Text {
                    id: statusText
                    x: 30
                    width: parent.width - 42
                    anchors.verticalCenter: parent.verticalCenter
                    text: backend.status
                    color: "#bcd0f5"
                    elide: Text.ElideRight
                    font.pixelSize: 12
                }
            }
            Btn { label: "Join with code"; primary: false; onClicked: joinPopup.open() }
            Btn {
                label: "Pair a device"
                onClicked: if (backend.openPairing()) pairPopup.open()
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 16

            // ---------------- left: gesture studio ----------------
            Card {
                Layout.fillWidth: true
                Layout.fillHeight: true
                title: "GESTURE STUDIO"

                Rectangle {
                    id: studio
                    anchors.fill: parent
                    anchors.leftMargin: 14; anchors.rightMargin: 14; anchors.bottomMargin: 74
                    anchors.topMargin: -2
                    radius: 14
                    color: "#060a14"
                    border.color: "#18254a"
                    clip: true

                    Image {
                        id: preview
                        anchors.fill: parent
                        fillMode: Image.PreserveAspectFit
                        cache: false
                        visible: backend.cameraActive
                        source: backend.cameraActive ? "image://camera/frame?n=" + backend.frameCounter : ""
                    }

                    Column {
                        anchors.horizontalCenter: parent.horizontalCenter
                        y: 26
                        spacing: 6
                        visible: !backend.cameraActive && !backend.hasFile
                        Text {
                            anchors.horizontalCenter: parent.horizontalCenter
                            text: backend.cameraStarting ? "Starting camera…" : "Your gesture space"
                            color: root.textColor
                            font.pixelSize: 20
                            font.weight: Font.DemiBold
                        }
                        Text {
                            anchors.horizontalCenter: parent.horizontalCenter
                            text: "Choose a file, start the camera, grab it and throw."
                            color: root.mutedColor
                            font.pixelSize: 13
                        }
                    }

                    // hand indicator
                    Rectangle {
                        id: handRing
                        visible: backend.cameraActive && backend.handPresent && backend.senderPhase !== "holding"
                        width: 34; height: 34; radius: 17
                        color: "transparent"
                        border.color: root.cyan
                        border.width: 2
                        opacity: 0.8
                        x: preview.x + (preview.width - preview.paintedWidth) / 2 + backend.handX * preview.paintedWidth - width / 2
                        y: preview.y + (preview.height - preview.paintedHeight) / 2 + backend.handY * preview.paintedHeight - height / 2
                        Behavior on x { NumberAnimation { duration: 70 } }
                        Behavior on y { NumberAnimation { duration: 70 } }
                    }

                    // ---- the sender's file card: zooms out on grab, flies off on throw ----
                    Item {
                        id: sc
                        width: 232; height: 244
                        property real offX: 0
                        property real offY: 0
                        property real k: 1.0
                        property bool alert: false
                        property string lastPhase: "idle"
                        readonly property bool following: backend.senderPhase === "holding" && backend.handPresent && backend.cameraActive
                        readonly property real homeX: studio.width / 2 - width / 2
                        readonly property real homeY: studio.height / 2 - height / 2 + 8
                        readonly property real handPx: preview.x + (preview.width - preview.paintedWidth) / 2 + backend.handX * preview.paintedWidth
                        readonly property real handPy: preview.y + (preview.height - preview.paintedHeight) / 2 + backend.handY * preview.paintedHeight
                        x: (following ? handPx - width / 2 : homeX) + offX
                        y: (following ? handPy - height / 2 : homeY) + offY
                        scale: k
                        opacity: 0
                        visible: backend.hasFile && opacity > 0.01
                        Behavior on x { enabled: sc.following; NumberAnimation { duration: 90; easing.type: Easing.OutQuad } }
                        Behavior on y { enabled: sc.following; NumberAnimation { duration: 90; easing.type: Easing.OutQuad } }

                        Rectangle {      // halo
                            anchors.fill: parent
                            anchors.margins: -10
                            radius: 34
                            color: sc.alert ? root.red : root.cyan
                            opacity: backend.senderPhase === "holding" ? 0.22 : 0.10
                            Behavior on opacity { NumberAnimation { duration: 200 } }
                        }
                        Rectangle {
                            anchors.fill: parent
                            radius: 26
                            color: "#0e1630"
                            border.width: 2
                            border.color: sc.alert ? root.red : "#2f6fb0"
                            Behavior on border.color { ColorAnimation { duration: 200 } }
                            FileThumb {
                                x: 14; y: 14
                                width: parent.width - 28; height: 158
                                hasImage: backend.hasThumb
                                source: "image://thumb/send/" + backend.thumbVersion
                                badge: backend.fileBadge
                                kind: backend.fileKind
                            }
                            Text {
                                x: 16; y: 182
                                width: parent.width - 32
                                text: backend.fileName
                                color: root.textColor
                                elide: Text.ElideMiddle
                                font.pixelSize: 14
                                font.weight: Font.DemiBold
                            }
                            Text {
                                x: 16; y: 206
                                text: backend.fileSize
                                color: root.mutedColor
                                font.pixelSize: 12
                            }
                        }

                        NumberAnimation { id: zoomOut; target: sc; property: "k"; to: backend.reducedMotion ? 1 : 0.5; duration: root.d(260); easing.type: Easing.OutCubic }
                        NumberAnimation { id: zoomBack; target: sc; property: "k"; to: 1; duration: root.d(320); easing.type: Easing.OutBack; easing.overshoot: 1.2 }
                        SequentialAnimation {
                            id: launch
                            NumberAnimation { target: sc; property: "k"; to: backend.reducedMotion ? 1 : 0.5; duration: sc.k > 0.7 ? root.d(180) : 1; easing.type: Easing.OutCubic }
                            ParallelAnimation {
                                NumberAnimation { target: sc; property: "offX"; to: backend.reducedMotion ? 0 : backend.throwDx * Math.max(studio.width, studio.height) * 0.8; duration: root.d(430); easing.type: Easing.InCubic }
                                NumberAnimation { target: sc; property: "offY"; to: backend.reducedMotion ? 0 : backend.throwDy * Math.max(studio.width, studio.height) * 0.8; duration: root.d(430); easing.type: Easing.InCubic }
                                NumberAnimation { target: sc; property: "k"; to: backend.reducedMotion ? 1 : 0.16; duration: root.d(430); easing.type: Easing.InCubic }
                                NumberAnimation { target: sc; property: "opacity"; to: 0; duration: root.d(430); easing.type: Easing.InQuad }
                            }
                        }
                        SequentialAnimation {
                            id: popIn       // the card (re)appears: from small to full size
                            PropertyAction { target: sc; property: "offX"; value: 0 }
                            PropertyAction { target: sc; property: "offY"; value: 0 }
                            PropertyAction { target: sc; property: "k"; value: backend.reducedMotion ? 1 : 0.6 }
                            ParallelAnimation {
                                NumberAnimation { target: sc; property: "k"; to: 1; duration: root.d(380); easing.type: Easing.OutBack; easing.overshoot: 1.3 }
                                NumberAnimation { target: sc; property: "opacity"; to: 1; duration: root.d(240) }
                            }
                        }
                        SequentialAnimation {
                            id: scShake
                            NumberAnimation { target: sc; property: "offX"; to: backend.reducedMotion ? 0 : -12; duration: root.d(60) }
                            NumberAnimation { target: sc; property: "offX"; to: backend.reducedMotion ? 0 : 12; duration: root.d(90) }
                            NumberAnimation { target: sc; property: "offX"; to: backend.reducedMotion ? 0 : -8; duration: root.d(80) }
                            NumberAnimation { target: sc; property: "offX"; to: 0; duration: root.d(70) }
                        }
                        function onPhase(p) {
                            var was = lastPhase
                            lastPhase = p
                            if (p === "holding") {
                                popIn.stop(); zoomBack.stop(); zoomOut.restart()
                            } else if (p === "launched") {
                                zoomOut.stop(); zoomBack.stop(); popIn.stop(); launch.restart()
                            } else if (p === "idle" || p === "failed") {
                                alert = (p === "failed")
                                launch.stop(); zoomOut.stop()
                                if (was === "holding") { zoomBack.restart() }
                                else if (backend.hasFile && (opacity < 0.99 || k < 0.99)) { popIn.restart() }
                                else if (p === "failed") { scShake.restart() }
                            }
                        }
                        Connections {
                            target: backend
                            function onSenderPhaseChanged() { sc.onPhase(backend.senderPhase) }
                            function onHasFileChanged() {
                                if (backend.hasFile) { sc.alert = false; popIn.restart() }
                                else { sc.opacity = 0 }
                            }
                        }
                    }

                    // ---- status chip after the throw ----
                    Rectangle {
                        id: sendChip
                        readonly property bool shown: ["waiting", "sending", "done", "failed"].indexOf(backend.senderPhase) >= 0
                        anchors.horizontalCenter: parent.horizontalCenter
                        y: shown ? 50 : 38
                        width: Math.min(parent.width - 40, 440)
                        height: backend.senderPhase === "failed" ? 104 : 66
                        Behavior on height { NumberAnimation { duration: root.d(200) } }
                        radius: 18
                        color: "#0e1a36"
                        border.color: backend.senderPhase === "done" ? root.green
                                      : backend.senderPhase === "failed" ? root.red : "#2f6fb0"
                        opacity: shown ? 1 : 0
                        visible: opacity > 0.01
                        Behavior on opacity { NumberAnimation { duration: root.d(220) } }
                        Behavior on y { NumberAnimation { duration: root.d(280); easing.type: Easing.OutCubic } }
                        Text {
                            x: 18; y: 10
                            width: parent.width - 36
                            wrapMode: Text.WordWrap
                            maximumLineCount: backend.senderPhase === "failed" ? 4 : 2
                            elide: Text.ElideRight
                            color: backend.senderPhase === "done" ? root.green
                                   : backend.senderPhase === "failed" ? root.red : root.textColor
                            font.pixelSize: 14
                            font.weight: Font.DemiBold
                            text: backend.senderPhase === "waiting" ? (backend.sendStep !== "" ? backend.sendStep : "Waiting for " + backend.selectedDeviceName + " to catch it...")
                                  : backend.senderPhase === "sending" ? "Sending to " + backend.selectedDeviceName + " · " + Math.round(backend.sendProgress * 100) + "%"
                                  : backend.senderMessage
                        }
                        Rectangle {
                            x: 18
                            anchors.bottom: parent.bottom; anchors.bottomMargin: 12
                            width: parent.width - 36; height: 6; radius: 3
                            color: "#16264a"
                            Rectangle {
                                width: parent.width * (backend.senderPhase === "waiting" ? 0.0 : backend.sendProgress)
                                height: parent.height; radius: 3
                                color: backend.senderPhase === "failed" ? root.red : backend.senderPhase === "done" ? root.green : root.cyan
                                Behavior on width { NumberAnimation { duration: 120 } }
                            }
                        }
                    }

                    // bottom overlay: hint + progress
                    Rectangle {
                        visible: backend.cameraActive
                        anchors.left: parent.left; anchors.bottom: parent.bottom
                        anchors.margins: 12
                        height: 30; radius: 15
                        width: hintText.implicitWidth + 28
                        color: "#cc07101f"
                        border.color: "#25406e"
                        Text { id: hintText; anchors.centerIn: parent; text: backend.gestureHint; color: "#cfe3ff"; font.pixelSize: 12 }
                    }
                    Rectangle {
                        visible: backend.cameraActive && backend.lastGesture !== ""
                        anchors.left: parent.left; anchors.top: parent.top
                        anchors.margins: 12
                        height: 28; radius: 14
                        width: Math.min(parent.width - 190, lastText.implicitWidth + 26)
                        color: "#cc07101f"
                        border.color: backend.lastGesture.indexOf("NOT SENT") === 0 ? root.red : "#25406e"
                        Text {
                            id: lastText
                            anchors.centerIn: parent
                            width: parent.width - 20
                            elide: Text.ElideRight
                            horizontalAlignment: Text.AlignHCenter
                            text: backend.lastGesture
                            color: backend.lastGesture.indexOf("NOT SENT") === 0 ? root.red : root.textColor
                            font.pixelSize: 11
                            font.weight: Font.DemiBold
                        }
                    }
                    Rectangle {
                        visible: backend.cameraActive
                        anchors.right: parent.right; anchors.top: parent.top
                        anchors.margins: 12
                        height: 28; radius: 14
                        width: stateText.implicitWidth + 26
                        color: "#cc07101f"
                        border.color: "#25406e"
                        Text { id: stateText; anchors.centerIn: parent; text: backend.mode.toUpperCase() + " · " + backend.gestureState; color: root.cyan; font.pixelSize: 11; font.weight: Font.Bold }
                    }
                    Rectangle {
                        visible: backend.cameraActive
                        anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
                        height: 4; color: "#10193a"
                        Rectangle {
                            width: parent.width * backend.gestureProgress; height: parent.height; color: root.cyan
                            Behavior on width { NumberAnimation { duration: 80 } }
                        }
                    }
                }

                RowLayout {
                    anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
                    anchors.margins: 16
                    height: 40
                    spacing: 10
                    Btn {
                        label: backend.cameraStarting ? "Starting…" : backend.cameraActive ? "Stop camera" : "Start camera"
                        onClicked: backend.toggleCamera()
                    }
                    Btn { label: "SEND"; primary: backend.mode === "send"; onClicked: backend.setSendMode() }
                    Btn { label: "RECEIVE"; primary: backend.mode === "receive"; onClicked: backend.setReceiveMode() }
                    Item { Layout.fillWidth: true }
                    Text {
                        text: "Easy throw"
                        color: backend.easyThrow ? root.cyan : root.mutedColor
                        font.pixelSize: 12
                        font.weight: Font.DemiBold
                        MouseArea { anchors.fill: parent; anchors.margins: -4; cursorShape: Qt.PointingHandCursor; onClicked: backend.toggleEasyThrow() }
                    }
                    Rectangle {
                        width: 34; height: 18; radius: 9
                        color: backend.easyThrow ? "#0e5f73" : "#16264a"
                        Rectangle {
                            width: 14; height: 14; radius: 7; y: 2
                            x: backend.easyThrow ? 18 : 2
                            color: backend.easyThrow ? root.cyan : "#7f8fb8"
                            Behavior on x { NumberAnimation { duration: 120 } }
                        }
                        MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: backend.toggleEasyThrow() }
                    }
                }
            }

            // ---------------- right column ----------------
            ColumnLayout {
                Layout.preferredWidth: 380
                Layout.maximumWidth: 380
                Layout.fillHeight: true
                spacing: 14

                Card {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 220
                    title: "DEVICES"
                    ListView {
                        id: deviceList
                        x: 12; y: 0
                        width: parent.width - 24
                        height: 98
                        clip: true
                        spacing: 6
                        model: backend.devices
                        delegate: Rectangle {
                            id: row
                            property bool arm: false
                            width: deviceList.width
                            height: 46
                            radius: 12
                            color: index === backend.selectedDevice ? "#12305a" : "#0f1932"
                            border.color: index === backend.selectedDevice ? root.cyan : "#1d2d55"
                            opacity: paired ? 1 : 0.6
                            Rectangle {
                                x: 12; anchors.verticalCenter: parent.verticalCenter
                                width: 9; height: 9; radius: 5
                                color: !paired ? root.mutedColor : online ? root.green : "#55627f"
                            }
                            Column {
                                x: 30; anchors.verticalCenter: parent.verticalCenter
                                Text { width: row.width - 112; elide: Text.ElideRight; text: name; color: root.textColor; font.pixelSize: 13; font.weight: Font.DemiBold }
                                Text { width: row.width - 112; elide: Text.ElideRight; text: status + (address ? " · " + address : ""); color: root.mutedColor; font.pixelSize: 11 }
                            }
                            Text {
                                visible: paired && index === backend.selectedDevice
                                anchors.right: parent.right; anchors.rightMargin: 12
                                anchors.verticalCenter: parent.verticalCenter
                                text: row.arm ? "Sure?" : "Unpair"
                                color: row.arm ? root.red : root.mutedColor
                                font.pixelSize: 11
                                MouseArea {
                                    anchors.fill: parent; anchors.margins: -8
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: { if (row.arm) backend.unpairDevice(index); else { row.arm = true; armTimer.restart() } }
                                }
                                Timer { id: armTimer; interval: 2500; onTriggered: row.arm = false }
                            }
                            MouseArea {
                                anchors.fill: parent
                                z: -1
                                cursorShape: paired ? Qt.PointingHandCursor : Qt.ArrowCursor
                                onClicked: backend.selectDevice(index)
                            }
                        }
                        Text {
                            anchors.centerIn: parent
                            visible: deviceList.count === 0
                            width: parent.width - 20
                            horizontalAlignment: Text.AlignHCenter
                            wrapMode: Text.WordWrap
                            text: "No devices yet.\nUse “Pair a device” on one laptop and “Join with code” on the other."
                            color: root.mutedColor
                            font.pixelSize: 12
                        }
                    }
                    TextField {
                        x: 12; y: 106
                        width: parent.width - 24; height: 34
                        placeholderText: "Address if not found, e.g. 192.168.1.23:5001"
                        placeholderTextColor: "#51628d"
                        color: root.textColor
                        font.pixelSize: 12
                        selectByMouse: true
                        leftPadding: 12
                        background: Rectangle { radius: 10; color: "#0a1226"; border.color: "#1d2d55" }
                        onTextChanged: backend.setManualAddress(text)
                    }
                    Btn {
                        x: 12; y: 148
                        label: backend.testingConnection ? "Testing…" : "Test connection"
                        primary: false
                        active: !backend.testingConnection
                        implicitHeight: 30
                        onClicked: backend.testConnection()
                    }
                    Text {
                        x: 150; y: 146
                        width: parent.width - 162
                        height: 44
                        wrapMode: Text.WordWrap
                        maximumLineCount: 3
                        elide: Text.ElideRight
                        text: backend.connectionNote
                        color: backend.connectionOk ? root.green : backend.testingConnection ? root.mutedColor : root.red
                        font.pixelSize: 11
                    }
                }

                Card {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 210
                    title: "FILE"
                    Row {
                        x: 16; y: 2
                        spacing: 12
                        visible: backend.hasFile
                        FileThumb {
                            width: 64; height: 64; radius: 12
                            hasImage: backend.hasThumb
                            source: "image://thumb/send/" + backend.thumbVersion
                            badge: backend.fileBadge
                            kind: backend.fileKind
                        }
                        Column {
                            anchors.verticalCenter: parent.verticalCenter
                            spacing: 3
                            Text { width: 250; text: backend.fileName; color: root.textColor; font.pixelSize: 14; font.weight: Font.DemiBold; elide: Text.ElideMiddle }
                            Text { text: backend.fileSize; color: root.mutedColor; font.pixelSize: 12 }
                            Text {
                                text: "Remove"
                                color: root.mutedColor
                                font.pixelSize: 11
                                MouseArea { anchors.fill: parent; anchors.margins: -6; cursorShape: Qt.PointingHandCursor; onClicked: backend.clearFile() }
                            }
                        }
                    }
                    Text {
                        x: 16; y: 14
                        visible: !backend.hasFile
                        width: parent.width - 32
                        wrapMode: Text.WordWrap
                        text: "Drop a file anywhere in this window, or choose one."
                        color: root.mutedColor
                        font.pixelSize: 12
                    }
                    ColumnLayout {
                        anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
                        anchors.margins: 16
                        spacing: 8
                        // row 1: the two ways to pick a file, side by side
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 8
                            Btn { Layout.fillWidth: true; Layout.preferredWidth: 100; label: "Choose file"; primary: false; onClicked: fileDialog.open() }
                            Btn { Layout.fillWidth: true; Layout.preferredWidth: 100; label: "Browse ✌"; primary: false; onClicked: backend.openBrowser() }
                        }
                        // row 2: one long button for sending, so a long PC name still fits
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 8
                            Btn {
                                Layout.fillWidth: true; Layout.preferredWidth: 100
                                implicitHeight: 40
                                label: ["launched", "waiting", "sending"].indexOf(backend.senderPhase) >= 0 ? "Sending…"
                                       : backend.selectedDeviceName ? "Send to " + backend.selectedDeviceName : "Send"
                                active: backend.canSend
                                onClicked: backend.sendSelected()
                            }
                            Btn {
                                visible: ["launched", "waiting", "sending"].indexOf(backend.senderPhase) >= 0
                                implicitHeight: 40
                                label: "Cancel"; primary: false
                                onClicked: backend.cancelSend()
                            }
                        }
                    }
                }

                Card {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 84
                    title: "TRANSFER"
                    Text {
                        x: 18; y: -4
                        width: parent.width - 90
                        text: backend.transferLabel
                        color: root.mutedColor
                        elide: Text.ElideRight
                        font.pixelSize: 12
                    }
                    Text {
                        anchors.right: parent.right; anchors.rightMargin: 18
                        y: -4
                        text: Math.round(backend.transferProgress * 100) + "%"
                        color: root.cyan
                        font.pixelSize: 12
                        font.weight: Font.Bold
                    }
                    Rectangle {
                        x: 18; y: 20
                        width: parent.width - 36; height: 8; radius: 4
                        color: "#14224a"
                        Rectangle {
                            width: parent.width * backend.transferProgress; height: parent.height; radius: 4
                            color: root.cyan
                            Behavior on width { NumberAnimation { duration: 120 } }
                        }
                    }
                }

                Card {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Layout.minimumHeight: 100
                    title: "ACTIVITY"
                    ListView {
                        x: 14; y: -4
                        width: parent.width - 28
                        height: parent.height - 34
                        clip: true
                        spacing: 4
                        model: backend.history
                        delegate: Row {
                            spacing: 8
                            width: ListView.view.width
                            Text { text: modelData.when; color: "#51628d"; font.pixelSize: 11; width: 38 }
                            Text { text: modelData.title; color: root.cyan; font.pixelSize: 12; font.weight: Font.DemiBold; width: 78 }
                            Text { text: modelData.detail; color: "#a9bbe3"; font.pixelSize: 12; width: parent.width - 130; elide: Text.ElideRight }
                        }
                        Text {
                            anchors.centerIn: parent
                            visible: parent.count === 0
                            text: "Your activity will appear here"
                            color: "#51628d"; font.pixelSize: 12
                        }
                    }
                    Text {
                        anchors.right: parent.right; anchors.rightMargin: 16
                        y: -28
                        text: "Open log folder"
                        color: root.mutedColor
                        font.pixelSize: 11
                        MouseArea { anchors.fill: parent; anchors.margins: -6; cursorShape: Qt.PointingHandCursor; onClicked: backend.openLogFolder() }
                    }
                    Row {
                        anchors.right: parent.right; anchors.rightMargin: 16
                        anchors.bottom: parent.bottom; anchors.bottomMargin: 8
                        spacing: 8
                        Text { text: "Reduce motion"; color: root.mutedColor; font.pixelSize: 11; anchors.verticalCenter: parent.verticalCenter }
                        Rectangle {
                            width: 34; height: 18; radius: 9
                            color: backend.reducedMotion ? "#0e5f73" : "#16264a"
                            Rectangle {
                                width: 14; height: 14; radius: 7; y: 2
                                x: backend.reducedMotion ? 18 : 2
                                color: backend.reducedMotion ? root.cyan : "#7f8fb8"
                                Behavior on x { NumberAnimation { duration: 120 } }
                            }
                            MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: backend.toggleReducedMotion() }
                        }
                    }
                }
            }
        }
    }

    // ---- the receiver's incoming card: pops up from the bottom, zooms in when verified ---------
    Item {
        id: stage
        anchors.fill: parent
        z: 100
        visible: scrim.opacity > 0.01 || ic.opacity > 0.01

        Rectangle {
            id: scrim
            anchors.fill: parent
            color: "#02050d"
            opacity: 0
        }
        MouseArea { anchors.fill: parent; enabled: stage.visible }

        Item {
            id: ic
            width: 340; height: 408
            property real offX: 0
            property real offY: 0
            property real floatY: 0
            property real k: 1
            readonly property string phase: backend.receivePhase
            readonly property real hiddenOffset: stage.height / 2 + height * 0.65
            x: stage.width / 2 - width / 2 + offX
            y: stage.height / 2 - height / 2 + offY + floatY
            scale: k
            opacity: 0

            Rectangle {      // glow
                anchors.fill: parent; anchors.margins: -14; radius: 40
                color: ic.phase === "failed" ? root.red : ic.phase === "arrived" ? root.green : root.cyan
                opacity: ic.phase === "offered" ? glowPulse : 0.12
                property real glowPulse: 0.16
                id: halo
                SequentialAnimation on glowPulse {
                    running: ic.phase === "offered" && !backend.reducedMotion
                    loops: Animation.Infinite
                    NumberAnimation { to: 0.30; duration: 900; easing.type: Easing.InOutSine }
                    NumberAnimation { to: 0.14; duration: 900; easing.type: Easing.InOutSine }
                }
            }
            Rectangle {
                id: icBody
                anchors.fill: parent
                radius: 30
                color: "#0e1630"
                border.width: 2
                border.color: ic.phase === "failed" ? root.red : ic.phase === "arrived" ? root.green : "#2f6fb0"
                Behavior on border.color { ColorAnimation { duration: 240 } }

                Item {
                    id: thumbHolder
                    x: 30; y: 28
                    width: parent.width - 60; height: 196
                    property real z2: 1
                    scale: z2
                    FileThumb {
                        anchors.fill: parent
                        hasImage: backend.hasIncomingThumb
                        source: "image://thumb/recv/" + backend.incomingThumbVersion
                        badge: backend.incomingBadge
                        kind: backend.incomingKind
                    }
                    // progress ring on the thumbnail corner
                    Item {
                        id: ring
                        width: 62; height: 62
                        anchors.right: parent.right; anchors.bottom: parent.bottom
                        anchors.rightMargin: -14; anchors.bottomMargin: -14
                        property real badgeScale: 1
                        scale: badgeScale
                        Rectangle { anchors.fill: parent; radius: 31; color: "#0b1226"; border.color: "#1d2d55" }
                        Shape {
                            anchors.fill: parent
                            preferredRendererType: Shape.CurveRenderer
                            ShapePath {
                                strokeColor: "#1f3158"; strokeWidth: 5; fillColor: "transparent"
                                PathAngleArc { centerX: 31; centerY: 31; radiusX: 25; radiusY: 25; startAngle: 0; sweepAngle: 360 }
                            }
                            ShapePath {
                                strokeColor: ic.phase === "failed" ? root.red : ic.phase === "arrived" ? root.green : root.cyan
                                strokeWidth: 5; fillColor: "transparent"; capStyle: ShapePath.RoundCap
                                PathAngleArc {
                                    centerX: 31; centerY: 31; radiusX: 25; radiusY: 25; startAngle: -90
                                    sweepAngle: ic.phase === "arrived" ? 360 : 360 * backend.receiveProgress
                                    Behavior on sweepAngle { NumberAnimation { duration: 120 } }
                                }
                            }
                        }
                        Text {
                            anchors.centerIn: parent
                            font.pixelSize: ic.phase === "arrived" || ic.phase === "failed" || ic.phase === "offered" ? 24 : 13
                            font.weight: Font.Bold
                            color: ic.phase === "failed" ? root.red : ic.phase === "arrived" ? root.green : root.cyan
                            text: ic.phase === "arrived" ? "✓" : ic.phase === "failed" ? "!"
                                  : ic.phase === "offered" ? "↓" : Math.round(backend.receiveProgress * 100) + "%"
                        }
                    }
                }

                Text {
                    x: 30; y: 244
                    width: parent.width - 60
                    text: backend.incomingName
                    color: root.textColor
                    elide: Text.ElideMiddle
                    font.pixelSize: 17
                    font.weight: Font.DemiBold
                }
                Text {
                    x: 30; y: 272
                    width: parent.width - 60
                    elide: Text.ElideRight
                    text: "from " + backend.incomingSender + " · " + backend.incomingSize
                    color: root.mutedColor
                    font.pixelSize: 13
                }
                Text {
                    x: 30; y: 296
                    width: parent.width - 60
                    elide: Text.ElideRight
                    font.pixelSize: 12
                    color: ic.phase === "failed" ? root.red : ic.phase === "arrived" ? root.green : root.cyan
                    text: ic.phase === "offered" ? (backend.cameraActive ? (backend.gestureState === "CATCH ARMED" ? "Now open your hand to receive" : "Close your fist, then open it to receive") : "Press Catch to accept")
                          : ic.phase === "receiving" ? "Receiving…"
                          : ic.phase === "verifying" ? "Checking the file arrived intact…"
                          : ic.phase === "arrived" ? "Saved: " + backend.receivedName
                          : backend.receiveMessage
                }
                Row {
                    x: 30; y: 340
                    spacing: 10
                    visible: ic.phase === "offered"
                    Btn { label: "Catch"; onClicked: backend.acceptIncoming() }
                    Btn { label: "Decline"; primary: false; onClicked: backend.declineIncoming() }
                }
                Row {
                    x: 30; y: 340
                    spacing: 10
                    visible: ic.phase === "arrived"
                    Btn { label: "Open"; onClicked: backend.openReceived() }
                    Btn { label: "Show folder"; primary: false; onClicked: backend.showReceivedFolder() }
                    Btn { label: "Done"; primary: false; onClicked: backend.dismissIncoming() }
                }
                Row {
                    x: 30; y: 340
                    visible: ic.phase === "failed"
                    Btn { label: "Close"; primary: false; onClicked: backend.dismissIncoming() }
                }
            }

            SequentialAnimation on floatY {
                id: floatAnim
                running: ic.phase === "offered" && !backend.reducedMotion
                loops: Animation.Infinite
                NumberAnimation { to: -6; duration: 1100; easing.type: Easing.InOutSine }
                NumberAnimation { to: 6; duration: 1100; easing.type: Easing.InOutSine }
                onRunningChanged: if (!running) ic.floatY = 0
            }

            ParallelAnimation {      // rises from below the screen to the middle
                id: popUp
                NumberAnimation { target: ic; property: "offY"; from: backend.reducedMotion ? 0 : ic.hiddenOffset; to: 0; duration: root.d(580); easing.type: Easing.OutBack; easing.overshoot: 0.9 }
                NumberAnimation { target: ic; property: "k"; from: backend.reducedMotion ? 1 : 0.8; to: 1; duration: root.d(520); easing.type: Easing.OutCubic }
                NumberAnimation { target: ic; property: "opacity"; from: 0; to: 1; duration: root.d(220) }
                NumberAnimation { target: scrim; property: "opacity"; to: 0.55; duration: root.d(320) }
            }
            SequentialAnimation {    // the catch: a small pulse
                id: catchPulse
                NumberAnimation { target: ic; property: "k"; to: backend.reducedMotion ? 1 : 1.05; duration: root.d(130); easing.type: Easing.OutQuad }
                NumberAnimation { target: ic; property: "k"; to: 1; duration: root.d(180); easing.type: Easing.InOutQuad }
            }
            ParallelAnimation {      // verified: the file zooms in
                id: zoomIn
                NumberAnimation { target: ic; property: "k"; to: backend.reducedMotion ? 1 : 1.12; duration: root.d(480); easing.type: Easing.OutCubic }
                NumberAnimation { target: thumbHolder; property: "z2"; to: backend.reducedMotion ? 1 : 1.07; duration: root.d(520); easing.type: Easing.OutCubic }
                NumberAnimation { target: scrim; property: "opacity"; to: 0.7; duration: root.d(400) }
                SequentialAnimation {
                    PropertyAction { target: ring; property: "badgeScale"; value: backend.reducedMotion ? 1 : 0.2 }
                    NumberAnimation { target: ring; property: "badgeScale"; to: 1; duration: root.d(420); easing.type: Easing.OutBack; easing.overshoot: 2.0 }
                }
            }
            SequentialAnimation {    // failure: a short shake
                id: shake
                NumberAnimation { target: ic; property: "offX"; to: backend.reducedMotion ? 0 : -12; duration: root.d(60) }
                NumberAnimation { target: ic; property: "offX"; to: backend.reducedMotion ? 0 : 12; duration: root.d(90) }
                NumberAnimation { target: ic; property: "offX"; to: backend.reducedMotion ? 0 : -8; duration: root.d(80) }
                NumberAnimation { target: ic; property: "offX"; to: 0; duration: root.d(70) }
            }
            ParallelAnimation {      // dismissed: slides back down
                id: leave
                NumberAnimation { target: ic; property: "offY"; to: backend.reducedMotion ? 0 : ic.hiddenOffset; duration: root.d(360); easing.type: Easing.InCubic }
                NumberAnimation { target: ic; property: "k"; to: backend.reducedMotion ? 1 : 0.88; duration: root.d(360); easing.type: Easing.InCubic }
                NumberAnimation { target: ic; property: "opacity"; to: 0; duration: root.d(320) }
                NumberAnimation { target: scrim; property: "opacity"; to: 0; duration: root.d(340) }
            }

            Connections {
                target: backend
                function onReceivePhaseChanged() {
                    var p = backend.receivePhase
                    if (p === "offered") {
                        leave.stop(); zoomIn.stop()
                        ic.offX = 0; thumbHolder.z2 = 1; ring.badgeScale = 1
                        popUp.restart()
                    } else if (p === "receiving") {
                        catchPulse.restart()
                    } else if (p === "arrived") {
                        catchPulse.stop(); zoomIn.restart()
                    } else if (p === "failed") {
                        shake.restart()
                    } else if (p === "none") {
                        popUp.stop(); zoomIn.stop(); shake.stop(); catchPulse.stop()
                        leave.restart()
                    }
                }
            }
        }
    }

    // ---- dialogs ---------------------------------------------------------------------------
    component DialogFrame: Popup {
        id: dlg
        property string heading: ""
        default property alias inner: slot.data
        modal: true
        anchors.centerIn: Overlay.overlay
        padding: 0
        closePolicy: Popup.CloseOnEscape
        enter: Transition {
            ParallelAnimation {
                NumberAnimation { property: "opacity"; from: 0; to: 1; duration: backend.reducedMotion ? 1 : 180 }
                NumberAnimation { property: "scale"; from: backend.reducedMotion ? 1 : 0.94; to: 1; duration: backend.reducedMotion ? 1 : 220; easing.type: Easing.OutCubic }
            }
        }
        exit: Transition { NumberAnimation { property: "opacity"; to: 0; duration: backend.reducedMotion ? 1 : 120 } }
        Overlay.modal: Rectangle { color: "#b0020510" }
        background: Rectangle { radius: 22; color: "#0e1630"; border.color: "#2a4a86"; border.width: 1 }
        contentItem: Item {
            implicitWidth: slot.childrenRect.width + 48
            implicitHeight: slot.childrenRect.height + 48
            Item { id: slot; x: 24; y: 24; width: parent.width - 48; height: childrenRect.height }
        }
    }

    DialogFrame {
        id: pairPopup
        width: 440
        onClosed: backend.closePairing()
        Column {
            width: 392
            spacing: 12
            Text { text: "Pair a new device"; color: root.textColor; font.pixelSize: 20; font.weight: Font.DemiBold }
            Text {
                width: parent.width; wrapMode: Text.WordWrap
                text: "On the other laptop open AirCatch, choose “Join with code” and paste the code below. This window works for 2 minutes and for one device."
                color: root.mutedColor; font.pixelSize: 13
            }
            Rectangle {
                anchors.horizontalCenter: parent.horizontalCenter
                width: 224; height: 224; radius: 14; color: "#e9fbff"
                Image {
                    anchors.fill: parent; anchors.margins: 8
                    source: backend.pairingUri ? "image://qr/code?v=" + backend.qrVersion : ""
                    fillMode: Image.PreserveAspectFit; smooth: false; cache: false
                }
            }
            TextField {
                id: codeField
                width: parent.width; height: 36
                readOnly: true
                text: backend.pairingUri
                color: "#a9bbe3"; font.pixelSize: 11; selectByMouse: true; leftPadding: 10
                background: Rectangle { radius: 10; color: "#0a1226"; border.color: "#1d2d55" }
            }
            Row {
                spacing: 10
                Btn { label: "Copy code"; onClicked: { codeField.selectAll(); codeField.copy(); codeField.deselect() } }
                Btn { label: "Close"; primary: false; onClicked: pairPopup.close() }
            }
        }
    }

    DialogFrame {
        id: requestPopup
        width: 420
        closePolicy: Popup.NoAutoClose
        property string who: ""
        property string where: ""
        Column {
            width: 372
            spacing: 14
            Text { text: "Allow this device to pair?"; color: root.textColor; font.pixelSize: 20; font.weight: Font.DemiBold }
            Text {
                width: parent.width; wrapMode: Text.WordWrap
                text: "“" + requestPopup.who + "” at " + requestPopup.where + " used your code. Only allow it if you just did this yourself."
                color: root.mutedColor; font.pixelSize: 13
            }
            Row {
                spacing: 10
                Btn { label: "Allow"; onClicked: { backend.answerPairing(true); requestPopup.close() } }
                Btn { label: "Deny"; primary: false; onClicked: { backend.answerPairing(false); requestPopup.close() } }
            }
        }
    }

    DialogFrame {
        id: joinPopup
        width: 460
        onOpened: { joinField.text = ""; joinResult.text = ""; joinField.forceActiveFocus() }
        Column {
            width: 412
            spacing: 12
            Text { text: "Join with a pairing code"; color: root.textColor; font.pixelSize: 20; font.weight: Font.DemiBold }
            Text {
                width: parent.width; wrapMode: Text.WordWrap
                text: "On the other laptop choose “Pair a device”, then copy its code and paste it here."
                color: root.mutedColor; font.pixelSize: 13
            }
            TextField {
                id: joinField
                width: parent.width; height: 38
                placeholderText: "aircatch://pair?…"
                placeholderTextColor: "#51628d"
                color: root.textColor; font.pixelSize: 12; selectByMouse: true; leftPadding: 10
                background: Rectangle { radius: 10; color: "#0a1226"; border.color: "#2a3d6e" }
                onAccepted: { joinResult.color = root.mutedColor; joinResult.text = "Connecting…"; backend.joinPairing(text) }
            }
            Text { id: joinResult; width: parent.width; wrapMode: Text.WordWrap; font.pixelSize: 12; color: root.mutedColor }
            Row {
                spacing: 10
                Btn { label: "Paste"; primary: false; onClicked: joinField.paste() }
                Btn {
                    label: "Join"
                    onClicked: { joinResult.color = root.mutedColor; joinResult.text = "Connecting… approve it on the other laptop."; backend.joinPairing(joinField.text) }
                }
                Btn { label: "Close"; primary: false; onClicked: joinPopup.close() }
            }
        }
    }

    Timer { id: joinCloser; interval: 1100; onTriggered: joinPopup.close() }

    Connections {
        target: backend
        function onPairingRequest(name, address) { requestPopup.who = name; requestPopup.where = address; requestPopup.open() }
        function onPairingClosed() { requestPopup.close() }
        function onPairingUriChanged() { if (backend.pairingUri === "" && pairPopup.opened) pairPopup.close() }
        function onJoinFinished(ok, text) {
            joinResult.color = ok ? root.green : root.red
            joinResult.text = text
            if (ok) joinCloser.restart()
        }
        function onToastChanged() { toastText.text = backend.toast; toast.shown = true; toastTimer.restart() }
    }

    // ---- gesture file explorer -----------------------------------------------------------------
    Rectangle {
        id: explorer
        z: 250
        anchors.fill: parent
        color: "#cc050a14"
        opacity: backend.browsing ? 1 : 0
        visible: opacity > 0.01
        Behavior on opacity { NumberAnimation { duration: root.d(180) } }
        MouseArea { anchors.fill: parent; onClicked: backend.closeBrowser() }

        Rectangle {
            id: panel
            anchors.centerIn: parent
            width: Math.min(640, parent.width - 60)
            height: Math.min(560, parent.height - 80)
            radius: 20
            color: root.cardColor; border.color: root.lineColor
            scale: backend.browsing ? 1 : 0.94
            Behavior on scale { NumberAnimation { duration: root.d(220); easing.type: Easing.OutCubic } }
            MouseArea { anchors.fill: parent }          // clicks inside must not close it

            Column {
                id: head
                x: 22; y: 18; width: parent.width - 44
                spacing: 4
                Text { text: "Choose a file"; color: root.textColor; font.pixelSize: 20; font.weight: Font.DemiBold }
                Text {
                    width: parent.width; elide: Text.ElideMiddle
                    text: backend.browserTitle; color: root.cyan; font.pixelSize: 12
                }
            }
            Btn {
                anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 16
                label: "Close"; primary: false
                onClicked: backend.closeBrowser()
            }

            ListView {
                id: rows
                anchors.left: parent.left; anchors.right: parent.right
                anchors.top: head.bottom; anchors.topMargin: 14
                anchors.bottom: foot.top; anchors.bottomMargin: 8
                anchors.leftMargin: 14; anchors.rightMargin: 14
                clip: true
                model: backend.browserModel
                currentIndex: backend.browserIndex
                // glide to the selected row (keeping a row of context above and below) instead of jumping
                highlightFollowsCurrentItem: true
                highlightRangeMode: ListView.ApplyRange
                preferredHighlightBegin: 46
                preferredHighlightEnd: Math.max(92, height - 92)
                highlightMoveDuration: root.d(140)
                highlightMoveVelocity: -1
                delegate: Rectangle {
                    width: rows.width; height: 46; radius: 11
                    readonly property bool active: index === backend.browserIndex
                    color: active ? "#12305a" : "transparent"
                    border.color: active ? root.cyan : "transparent"
                    border.width: active ? 2 : 0
                    Behavior on color { ColorAnimation { duration: root.d(100) } }
                    Rectangle {
                        id: icon
                        x: 10; anchors.verticalCenter: parent.verticalCenter
                        width: 30; height: 30; radius: 8
                        color: isDir ? "#1d4f7a" : "#243252"
                        Text {
                            anchors.centerIn: parent
                            text: isDir ? "📁" : badge
                            color: root.textColor; font.pixelSize: isDir ? 15 : 9; font.weight: Font.DemiBold
                        }
                    }
                    Text {
                        anchors.left: icon.right; anchors.leftMargin: 12
                        anchors.right: info.left; anchors.rightMargin: 8
                        anchors.verticalCenter: parent.verticalCenter
                        text: name; elide: Text.ElideMiddle
                        color: root.textColor; font.pixelSize: 14
                    }
                    Text {
                        id: info
                        anchors.right: parent.right; anchors.rightMargin: 14
                        anchors.verticalCenter: parent.verticalCenter
                        text: isDir ? "›" : detail; color: root.mutedColor; font.pixelSize: isDir ? 20 : 12
                    }
                    MouseArea { anchors.fill: parent; onClicked: backend.browserActivate(index) }
                }
                Text {
                    anchors.centerIn: parent
                    visible: backend.browserCount === 0
                    text: backend.browserNote; color: root.mutedColor; font.pixelSize: 14
                }
            }

            Column {
                id: foot
                anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
                anchors.margins: 18
                spacing: 4
                Text {
                    width: parent.width; wrapMode: Text.WordWrap; horizontalAlignment: Text.AlignHCenter
                    visible: backend.browserCount > 0 && backend.browserNote !== ""
                    text: backend.browserNote; color: root.amber; font.pixelSize: 12
                }
                Text {
                    width: parent.width; wrapMode: Text.WordWrap; horizontalAlignment: Text.AlignHCenter
                    text: "Swipe ↑ ↓ move  ·  → open folder  ·  ← back  ·  🤏 select  ·  ✌ close"
                    color: root.mutedColor; font.pixelSize: 12
                }
                Text {
                    width: parent.width; horizontalAlignment: Text.AlignHCenter
                    visible: backend.cameraActive
                    text: backend.gestureHint; color: root.cyan; font.pixelSize: 12
                }
            }
        }
    }

    // ---- toast -------------------------------------------------------------------------------
    Rectangle {
        id: toast
        property bool shown: false
        z: 300
        anchors.horizontalCenter: parent.horizontalCenter
        y: parent.height - (shown ? 78 : 56)
        width: Math.min(toastText.implicitWidth + 40, parent.width - 60)
        height: 40; radius: 20
        color: "#12203f"; border.color: "#2f6fb0"
        opacity: shown ? 1 : 0
        visible: opacity > 0.01
        Behavior on opacity { NumberAnimation { duration: root.d(200) } }
        Behavior on y { NumberAnimation { duration: root.d(240); easing.type: Easing.OutCubic } }
        Text { id: toastText; anchors.centerIn: parent; width: parent.width - 30; elide: Text.ElideRight; horizontalAlignment: Text.AlignHCenter; color: "#dbe8ff"; font.pixelSize: 13 }
        Timer { id: toastTimer; interval: 3600; onTriggered: toast.shown = false }
    }
}
