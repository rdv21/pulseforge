import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: root

    property string label: ""
    property var model: []
    property string selected: ""

    // ─── Signal ───
    signal deviceSelected(string name)

    readonly property color bgCard: "#16161f"
    readonly property color borderColor: "#252533"
    readonly property color accent: "#4a9eff"
    readonly property color textColor: "#c8c8d0"
    readonly property color textDim: "#6a6a78"

    implicitWidth: 200
    implicitHeight: 28

    RowLayout {
        anchors.fill: parent
        spacing: 4

        Text {
            text: root.label + ":"
            font.pixelSize: 10
            font.bold: true
            color: root.textDim
        }

        Rectangle {
            id: dropdown
            Layout.fillWidth: true
            height: 26
            radius: 4
            color: root.bgCard
            border.width: 1
            border.color: root.borderColor

            property bool open: false

            Text {
                anchors.left: parent.left
                anchors.leftMargin: 8
                anchors.verticalCenter: parent.verticalCenter
                text: root.selected
                font.pixelSize: 10
                color: root.textColor
                elide: Text.ElideRight
                width: parent.width - 24
            }

            Text {
                anchors.right: parent.right
                anchors.rightMargin: 6
                anchors.verticalCenter: parent.verticalCenter
                text: "▾"
                font.pixelSize: 10
                color: root.textDim
            }

            MouseArea {
                anchors.fill: parent
                cursorShape: Qt.PointingHandCursor
                onClicked: dropdown.open = !dropdown.open
            }
        }
    }

    // Popup renders in the window's overlay layer — always on top
    Popup {
        id: devicePopup
        parent: root
        y: dropdown.height + 2
        x: dropdown.x + (root.RowLayout ? 0 : 0)
        width: dropdown.width
        height: Math.min(listColumn.implicitHeight + 16, 300)
        padding: 4
        modal: false
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutsideParent
        visible: dropdown.open
        onVisibleChanged: if (!visible) dropdown.open = false

        background: Rectangle {
            color: root.bgCard
            border.width: 1
            border.color: root.borderColor
            radius: 4
        }

        contentItem: ScrollView {
            clip: true
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

            Column {
                id: listColumn
                spacing: 0

                Repeater {
                    model: root.model

                    Rectangle {
                        width: devicePopup.width - 8
                        height: 24
                        radius: 3
                        color: modelData === root.selected ? root.accent : "transparent"

                        Text {
                            anchors.left: parent.left
                            anchors.leftMargin: 8
                            anchors.verticalCenter: parent.verticalCenter
                            text: modelData
                            font.pixelSize: 10
                            color: modelData === root.selected ? "white" : root.textColor
                            elide: Text.ElideRight
                            width: parent.width - 16
                        }

                        MouseArea {
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            onClicked: {
                                root.selected = modelData
                                root.deviceSelected(modelData)
                                dropdown.open = false
                            }
                        }
                    }
                }
            }
        }
    }
}