import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: card

    // ─── Props ───
    property string title: ""
    property bool cardEnabled: true

    property string sliderLabel: ""
    property real sliderValue: 0
    property string sliderUnit: ""
    property real sliderMin: 0
    property real sliderMax: 100

    property string secondSliderLabel: ""
    property real secondSliderValue: 0
    property string secondSliderUnit: ""
    property real secondSliderMin: 0
    property real secondSliderMax: 100

    property string thirdSliderLabel: ""
    property real thirdSliderValue: 0
    property string thirdSliderUnit: ""
    property real thirdSliderMin: 0
    property real thirdSliderMax: 100

    // ─── Signals ───
    signal enabledToggled(bool enabled)
    signal sliderMoved(real value)
    signal secondSliderMoved(real value)
    signal thirdSliderMoved(real value)

    readonly property color bgCard: "#16161f"
    readonly property color borderColor: "#252533"
    readonly property color accent: "#4a9eff"
    readonly property color textColor: "#c8c8d0"
    readonly property color textDim: "#6a6a78"
    readonly property color bgDark: "#0d0d14"

    radius: 8
    color: bgCard
    border.width: 1
    border.color: borderColor

    implicitHeight: content.implicitHeight + 20

    ColumnLayout {
        id: content
        anchors.fill: parent
        anchors.margins: 10
        spacing: 6

        // Title + Enable
        RowLayout {
            Layout.fillWidth: true
            spacing: 6

            Text {
                text: card.title
                font.pixelSize: 11
                font.bold: true
                color: card.textDim
                Layout.fillWidth: true
            }

            // Enable checkbox
            Rectangle {
                width: 18; height: 18
                radius: 3
                color: card.cardEnabled ? card.accent : card.bgDark
                border.width: 1
                border.color: card.cardEnabled ? card.accent : card.borderColor

                Text {
                    anchors.centerIn: parent
                    text: card.cardEnabled ? "✓" : ""
                    font.pixelSize: 12
                    font.bold: true
                    color: "white"
                }

                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: {
                        card.cardEnabled = !card.cardEnabled
                        card.enabledToggled(card.cardEnabled)
                    }
                }
            }
        }

        // Primary slider
        SliderRow {
            Layout.fillWidth: true
            label: card.sliderLabel
            value: card.sliderValue
            unit: card.sliderUnit
            min: card.sliderMin
            max: card.sliderMax
            onSliderMoved: function(value) {
                card.sliderValue = value
                card.sliderMoved(value)
            }
        }

        // Second slider (if present)
        SliderRow {
            Layout.fillWidth: true
            visible: card.secondSliderLabel !== ""
            label: card.secondSliderLabel
            value: card.secondSliderValue
            unit: card.secondSliderUnit
            min: card.secondSliderMin
            max: card.secondSliderMax
            onSliderMoved: function(value) {
                card.secondSliderValue = value
                card.secondSliderMoved(value)
            }
        }

        // Third slider (if present)
        SliderRow {
            Layout.fillWidth: true
            visible: card.thirdSliderLabel !== ""
            label: card.thirdSliderLabel
            value: card.thirdSliderValue
            unit: card.thirdSliderUnit
            min: card.thirdSliderMin
            max: card.thirdSliderMax
            onSliderMoved: function(value) {
                card.thirdSliderValue = value
                card.thirdSliderMoved(value)
            }
        }

        Item { Layout.fillHeight: true }
    }
}