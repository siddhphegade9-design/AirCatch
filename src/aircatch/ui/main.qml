import QtQuick
import QtQuick.Window

Window {
    id: root
    width: 960
    height: 680
    minimumWidth: 640
    minimumHeight: 480
    visible: true
    title: "AirCatch"
    color: "#0b1020"

    Rectangle {
        anchors.fill: parent
        gradient: Gradient {
            GradientStop { position: 0.0; color: "#0b1020" }
            GradientStop { position: 1.0; color: "#141a33" }
        }
    }

    Column {
        anchors.fill: parent
        anchors.margins: 28
        spacing: 20

        Row {
            spacing: 14
            Text {
                text: "AirCatch"
                color: "#e8f1ff"
                font.pixelSize: 34
                font.weight: Font.DemiBold
            }
            Rectangle {
                anchors.verticalCenter: parent.verticalCenter
                height: 28
                width: Math.min(statusText.implicitWidth + 28, 520)
                radius: 14
                color: backend.cameraActive ? "#0e3a4a" : "#1a2040"
                border.color: backend.cameraActive ? "#22d3ee" : "#3b4470"
                Text {
                    id: statusText
                    anchors.centerIn: parent
                    width: parent.width - 20
                    elide: Text.ElideRight
                    horizontalAlignment: Text.AlignHCenter
                    text: backend.status
                    color: backend.cameraActive ? "#22d3ee" : "#9aa6d6"
                    font.pixelSize: 13
                }
            }
        }

        Rectangle {
            width: parent.width
            height: parent.height - 140
            radius: 20
            color: "#0e1430"
            border.color: "#2a3566"
            border.width: 1

            Image {
                anchors.fill: parent
                anchors.margins: 10
                fillMode: Image.PreserveAspectFit
                cache: false
                visible: backend.cameraActive
                source: backend.cameraActive
                        ? "image://camera/frame?n=" + backend.frameCounter : ""
            }

            Text {
                anchors.centerIn: parent
                visible: !backend.cameraActive
                text: "Camera preview will appear here"
                color: "#5b6798"
                font.pixelSize: 18
            }
        }

        Rectangle {
            width: 200
            height: 46
            radius: 23
            color: mouse.containsMouse ? "#38bdf8" : "#22d3ee"
            Text {
                anchors.centerIn: parent
                text: backend.cameraActive ? "Stop camera" : "Start camera"
                color: "#06101f"
                font.pixelSize: 16
                font.weight: Font.DemiBold
            }
            MouseArea {
                id: mouse
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: backend.toggleCamera()
            }
        }
    }
}
