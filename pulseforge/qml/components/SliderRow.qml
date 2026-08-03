import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: row

    property string label: ""
    property real value: 0
    property string unit: ""
    property real min: 0
    property real max: 100

    readonly property color accent: "#4a9eff"
    readonly property color accentDim: "#2a5a8a"
    readonly property color bgDark: "#0d0d14"
    readonly property color textDim: "#6a6a78"

    // ─── Signal ───
    signal sliderMoved(real value)

    Layout.fillWidth: true
    implicitHeight: 40

    ColumnLayout {
        anchors.fill: parent
        spacing: 2

        Text {
            text: row.label
            font.pixelSize: 9
            color: row.textDim
        }

        // Slider
        Slider {
            id: sliderControl
            Layout.fillWidth: true
            from: row.min
            to: row.max
            value: row.value
            height: 18

            // Only emit signal on user interaction, not on programmatic value changes
            onMoved: row.sliderMoved(value)

            background: Rectangle {
                x: parent.leftPadding
                y: parent.topPadding + parent.availableHeight / 2 - 3
                width: parent.availableWidth
                height: 6
                radius: 3
                color: row.bgDark

                Rectangle {
                    width: parent.parent.visualPosition * parent.width
                    height: parent.height
                    radius: 3
                    color: row.accent
                    opacity: 0.6
                }
            }

            handle: Rectangle {
                x: parent.leftPadding + parent.visualPosition * (parent.availableWidth - width)
                y: parent.topPadding + parent.availableHeight / 2 - height / 2
                width: 14
                height: 14
                radius: 7
                color: parent.pressed ? "#3a8eef" : row.accent
                border.width: 2
                border.color: row.bgDark
            }

        }

        Text {
            text: Math.round(row.value) + row.unit
            font.pixelSize: 9
            font.family: "monospace"
            color: row.textDim
            Layout.alignment: Qt.AlignRight
        }
    }
}