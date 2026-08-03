import QtQuick
import QtQuick.Controls

Rectangle {
    id: btn

    property string btnText: ""
    property int preferredWidth: 60
    property bool checked: false

    readonly property color bgCard: "#16161f"
    readonly property color borderColor: "#252533"
    readonly property color accent: "#4a9eff"
    readonly property color textColor: "#c8c8d0"
    readonly property color textDim: "#6a6a78"

    width: preferredWidth
    height: 28
    radius: 5
    color: checked ? accent : bgCard
    border.width: 1
    border.color: checked ? accent : borderColor

    Text {
        anchors.centerIn: parent
        text: btn.btnText
        font.pixelSize: 11
        font.bold: true
        color: checked ? "white" : textDim
    }

    MouseArea {
        anchors.fill: parent
        cursorShape: Qt.PointingHandCursor
        onClicked: btn.checked = !btn.checked
    }
}