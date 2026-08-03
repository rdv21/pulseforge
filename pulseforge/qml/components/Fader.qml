import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: fader

    // ─── Public Props ───
    property string label: "Vol"
    property real maxValue: 1.0
    property real currentValue: 1.0
    property bool compact: false
    property bool showLabel: true
    property bool dragging: false

    // ─── Signal ───
    signal volChanged(real value)

    readonly property color accent: "#4a9eff"
    readonly property color accentDim: "#2a5a8a"
    readonly property color bgDark: "#0e0e16"
    readonly property color textColor: "#c8c8d0"
    readonly property color textDim: "#6a6a78"

    // ─── Internal ───
    // _sliderValue: 0.0 (bottom = mute) → 1.0 (top = 100%)
    property real _sliderValue: currentValue / maxValue
    property bool _dragging: false

    // Percentage display: 0% at bottom, 100% at top
    function pctDisplay(val) {
        return Math.round(val * 100) + "%";
    }

    Layout.fillWidth: true
    implicitHeight: compact ? 120 : 160

    ColumnLayout {
        anchors.fill: parent
        spacing: 2

        // Label (optional)
        Text {
            visible: fader.showLabel
            text: fader.label
            font.pixelSize: 10
            font.bold: true
            color: fader.textDim
            Layout.alignment: Qt.AlignHCenter
        }

        // Slider area (custom drawn)
        Item {
            id: sliderArea
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.alignment: Qt.AlignHCenter
            implicitWidth: 30

            // Groove
            Rectangle {
                id: groove
                anchors.horizontalCenter: parent.horizontalCenter
                width: 8
                height: parent.height - 16
                radius: 4
                color: fader.bgDark
                border.width: 1
                border.color: "#22222e"
                y: 8
            }

            // Fill (from bottom up to handle)
            Rectangle {
                id: fill
                anchors.horizontalCenter: parent.horizontalCenter
                width: 8
                radius: 4
                color: fader.accent
                opacity: 0.45

                y: groove.y + groove.height * (1 - fader._sliderValue)
                height: groove.height * fader._sliderValue
            }

            // Handle
            Rectangle {
                id: handle
                width: 26
                height: 14
                radius: 4
                color: fader.accent
                border.width: 2
                border.color: fader.bgDark
                anchors.horizontalCenter: parent.horizontalCenter

                y: groove.y + groove.height * (1 - fader._sliderValue) - height / 2

                // Center line
                Rectangle {
                    anchors.centerIn: parent
                    width: parent.width - 6
                    height: 1
                    color: Qt.rgba(0,0,0,0.3)
                }
            }

            // Drag area
            MouseArea {
                id: dragArea
                anchors.fill: parent
                anchors.leftMargin: -8
                anchors.rightMargin: -8
                cursorShape: Qt.SizeVerCursor

                property real startY: 0
                property real startVal: 0

                onPressed: {
                    fader._dragging = true;
                    fader.dragging = true;
                    startY = mouseY;
                    startVal = fader._sliderValue;
                    // Snap to click position
                    var clickY = mouseY - groove.y;
                    var newval = 1 - (clickY / groove.height);
                    fader._sliderValue = Math.max(0, Math.min(1, newval));
                    fader.volChanged(fader._sliderValue * fader.maxValue);
                }
                onPositionChanged: {
                    if (fader._dragging) {
                        var delta = (mouseY - startY) / groove.height;
                        var newval = startVal - delta;
                        fader._sliderValue = Math.max(0, Math.min(1, newval));
                        fader.volChanged(fader._sliderValue * fader.maxValue);
                    }
                }
                onReleased: { fader._dragging = false; fader.dragging = false }
                // Double-click resets to 100%
                onDoubleClicked: {
                    fader._sliderValue = 1.0;
                    fader.volChanged(fader._sliderValue * fader.maxValue);
                }
            }

            // Wheel support
            MouseArea {
                anchors.fill: parent
                acceptedButtons: Qt.NoButton
                onWheel: {
                    var step = 0.02;
                    if (wheel.angleDelta.y < 0) step = -step;
                    fader._sliderValue = Math.max(0, Math.min(1, fader._sliderValue + step));
                }
            }
        }
    }
}