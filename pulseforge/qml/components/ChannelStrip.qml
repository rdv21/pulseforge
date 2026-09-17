import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: strip

    // ─── Public Props ───
    property string name: ""
    property string channelName: name.toLowerCase()  // for backend matching
    property bool isMaster: false
    property bool isMic: false
    property bool showStreamFader: false
    property bool showMuteSolo: true
    property bool showApps: true
    property string appsText: ""

    // VU values pushed from backend (0.0-1.0+)
    property real mainVuValue: 0.0
    property real streamVuValue: 0.0

    // ─── Externally settable state (used by restoreStates) ───
    property real mainFaderValue: 1.0
    property real streamFaderValue: 1.0
    property bool muteChecked: false
    property bool streamChecked: false
    property bool mainFaderDragging: false
    property bool streamFaderDragging: false

    // ─── Signals ───
    signal muteToggled(bool muted)
    signal streamToggled(bool enabled)
    signal volChanged(real vol)
    signal streamVolChanged(real vol)
    signal settingsClicked()

    // ─── Theme ───
    readonly property color bgCard: "#16161f"
    readonly property color bgCardHover: "#1c1c28"
    readonly property color borderColor: "#252533"
    readonly property color accent: "#4a9eff"
    readonly property color textColor: "#c8c8d0"
    readonly property color textDim: "#7a7a88"
    readonly property color muteRed: "#c04040"
    readonly property color streamGreen: "#2a8a4a"
    readonly property color streamGreenBright: "#3aaa5a"

    property int controlWidth: 195

    color: bgCard
    radius: 10
    border.width: 1
    border.color: borderColor

    Rectangle {
        anchors.fill: parent
        radius: parent.radius
        gradient: Gradient {
            GradientStop { position: 0.0; color: "#1a1a26" }
            GradientStop { position: 1.0; color: "#14141c" }
        }
        opacity: 0.6
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 10
        spacing: 6

        // ─── Header ───
        RowLayout {
            Layout.fillWidth: true
            spacing: 4

            Text {
                text: strip.name
                font.pixelSize: 13
                font.bold: true
                color: strip.textColor
                horizontalAlignment: Text.AlignHCenter
                Layout.fillWidth: true
            }

            Rectangle {
                visible: !strip.isMaster
                width: 20; height: 20
                radius: 4
                color: "transparent"

                Text {
                    anchors.centerIn: parent
                    text: "⚙"
                    font.pixelSize: 14
                    color: strip.textDim
                }

                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    hoverEnabled: true
                    onEntered: parent.children[0].color = strip.accent
                    onExited: parent.children[0].color = strip.textDim
                    onClicked: strip.settingsClicked()
                }
            }
        }

        // ─── Stream / Mute buttons ───
        RowLayout {
            Layout.fillWidth: true
            visible: strip.showMuteSolo
            spacing: 4

            Rectangle {
                id: streamBtn
                property bool checked: strip.streamChecked
                Layout.fillWidth: true
                visible: strip.showStreamFader
                height: 26
                radius: 5
                color: checked ? strip.streamGreenBright : "#22222e"
                border.width: 1
                border.color: checked ? strip.streamGreen : "#33333f"

                Text {
                    anchors.centerIn: parent
                    text: "STREAM"
                    font.pixelSize: 10
                    font.bold: true
                    color: parent.checked ? "white" : "#888"
                }

                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: {
                        strip.streamChecked = !strip.streamChecked
                        strip.streamToggled(strip.streamChecked)
                    }
                }
            }

            Rectangle {
                id: muteBtn
                property bool checked: strip.muteChecked
                Layout.fillWidth: true
                height: 26
                radius: 5
                color: checked ? strip.muteRed : "#22222e"
                border.width: 1
                border.color: checked ? strip.muteRed : "#33333f"

                Text {
                    anchors.centerIn: parent
                    text: "MUTE"
                    font.pixelSize: 10
                    font.bold: true
                    color: parent.checked ? "white" : "#888"
                }

                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: {
                        strip.muteChecked = !strip.muteChecked
                        strip.muteToggled(strip.muteChecked)
                    }
                }
            }
        }

        // ─── Dual column: Stream (left) | Vol (right) ───
        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 6

            // ─── Stream column (left) ───
            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: 3
                visible: strip.showStreamFader

                Text {
                    text: "STR"
                    font.pixelSize: 10
                    font.bold: true
                    color: strip.streamGreen
                    Layout.alignment: Qt.AlignHCenter
                }

                VUMeter {
                    id: streamVU
                    Layout.alignment: Qt.AlignHCenter
                    Layout.preferredWidth: 22
                    Layout.fillHeight: true
                    Layout.preferredHeight: 100
                    tintGreen: "#3a9a5a"
                    tintYellow: "#aabd3a"
                    // Disable mock animation, use real data
                    mockAnimation: false
                    targetPeak: strip.streamVuValue
                }

                Fader {
                    id: streamFader
                    Layout.fillWidth: true
                    maxValue: 1.0
                    currentValue: strip.streamFaderValue
                    compact: true
                    showLabel: false
                    onVolChanged: strip.streamVolChanged(value)
                }

                Text {
                    text: Math.round(streamFader._sliderValue * 100) + "%"
                    font.pixelSize: 9
                    font.family: "monospace"
                    color: strip.streamGreen
                    Layout.alignment: Qt.AlignHCenter
                }
            }

            // ─── Volume column (right) ───
            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: 3

                Text {
                    text: "VOL"
                    font.pixelSize: 10
                    font.bold: true
                    color: strip.accent
                    Layout.alignment: Qt.AlignHCenter
                }

                VUMeter {
                    id: mainVU
                    Layout.alignment: Qt.AlignHCenter
                    Layout.preferredWidth: 22
                    Layout.fillHeight: true
                    Layout.preferredHeight: 100
                    mockAnimation: false
                    targetPeak: strip.mainVuValue
                }

                Fader {
                    id: volFader
                    Layout.fillWidth: true
                    maxValue: 1.0
                    currentValue: strip.mainFaderValue
                    compact: true
                    showLabel: false
                    onVolChanged: strip.volChanged(value)
                    onDraggingChanged: strip.mainFaderDragging = volFader.dragging
                }

                Text {
                    text: Math.round(volFader._sliderValue * 100) + "%"
                    font.pixelSize: 9
                    font.family: "monospace"
                    color: strip.accent
                    Layout.alignment: Qt.AlignHCenter
                }
            }
        }

        Text {
            visible: strip.showApps
            Layout.fillWidth: true
            text: strip.appsText
            font.pixelSize: 9
            color: strip.textDim
            wrapMode: Text.Wrap
            horizontalAlignment: Text.AlignHCenter
            elide: Text.ElideRight
            Layout.maximumHeight: 32
        }

        Item { Layout.fillHeight: true }
    }

    // ─── External control (fader sync from backend) ───
    function setExternalVolume(volume) {
        // Only update if user isn't actively dragging
        if (!strip.mainFaderDragging) {
            strip.mainFaderValue = volume
        }
    }
}