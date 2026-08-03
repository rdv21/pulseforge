import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: root

    property var apps: []
    signal appMoved(string appName, string group, int sinkInputId)

    readonly property color bgDark: "#0d0d14"
    readonly property color bgCard: "#16161f"
    readonly property color bgCardHover: "#1c1c28"
    readonly property color borderColor: "#252533"
    readonly property color accent: "#4a9eff"
    readonly property color accentDim: "#2a5a8a"
    readonly property color textColor: "#c8c8d0"
    readonly property color textDim: "#6a6a78"

    readonly property var groups: ["GAME", "CHAT", "MEDIA", "AUX"]

    // Card sizing
    readonly property int cardWidth: 153
    readonly property int cardHeight: 117
    readonly property int cardSpacing: 10

    color: bgDark
    radius: 0

    ColumnLayout {
        anchors.fill: parent
        spacing: 4

        // Header
        RowLayout {
            Layout.fillWidth: true
            Layout.leftMargin: 8
            Layout.rightMargin: 8
            spacing: 8

            Text {
                text: "Apps"
                font.pixelSize: 10
                font.bold: true
                color: root.textDim
            }

            Item { Layout.fillWidth: true }

            Text {
                text: root.apps.length + " active"
                font.pixelSize: 9
                color: root.textDim
            }
        }

        // Scrollable grid area
        ScrollView {
            id: appScroll
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            ScrollBar.vertical.policy: ScrollBar.AsNeeded

            // Grid container — uses a Flow to auto-wrap left-to-right then down
            Flow {
                id: gridContainer
                width: appScroll.width - 4
                spacing: root.cardSpacing
                leftPadding: 4
                rightPadding: 4
                topPadding: 4
                bottomPadding: 4

                Repeater {
                    model: root.apps

                    Rectangle {
                        id: appCard
                        width: root.cardWidth
                        height: root.cardHeight
                        radius: 8
                        color: mouseArea2.containsMouse ? root.bgCardHover : root.bgCard
                        border.width: 1
                        border.color: root.borderColor

                        property var appData: modelData
                        property int appId: modelData.id

                        // Group color accent bar at top
                        Rectangle {
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.top: parent.top
                            height: 3
                            radius: 8
                            color: {
                                var g = (appCard.appData.group || "aux").toUpperCase()
                                switch(g) {
                                    case "GAME": return "#4a9eff"
                                    case "CHAT": return "#d0a030"
                                    case "MEDIA": return "#a050d0"
                                    case "AUX": return "#50a080"
                                    default: return "#50a080"
                                }
                            }
                        }

                        ColumnLayout {
                            anchors.fill: parent
                            anchors.topMargin: 8
                            anchors.bottomMargin: 6
                            spacing: 2

                            // App name (top)
                            Text {
                                text: appCard.appData.name || "?"
                                font.pixelSize: 10
                                font.bold: true
                                color: root.textColor
                                Layout.alignment: Qt.AlignHCenter
                                Layout.fillWidth: true
                                horizontalAlignment: Text.AlignHCenter
                                elide: Text.ElideRight
                            }

                            // Central icon — system icon if available, fallback to letter
                            Item {
                                width: 43; height: 43
                                Layout.alignment: Qt.AlignHCenter

                                Rectangle {
                                    anchors.fill: parent
                                    radius: 9
                                    color: root.accent
                                    opacity: 0.15
                                }

                                Image {
                                    id: iconImg
                                    anchors.centerIn: parent
                                    width: 28; height: 28
                                    sourceSize.width: 28
                                    sourceSize.height: 28
                                    source: appCard.appData.icon ? "image://icons/" + appCard.appData.icon : ""
                                    visible: status === Image.Ready
                                    fillMode: Image.PreserveAspectFit
                                }

                                Text {
                                    anchors.centerIn: parent
                                    text: appCard.appData.name ? appCard.appData.name.charAt(0).toUpperCase() : "?"
                                    font.pixelSize: 22
                                    font.bold: true
                                    color: root.accent
                                    visible: !iconImg.visible || iconImg.status !== Image.Ready
                                }
                            }

                            Item { Layout.fillHeight: true }

                            // Channel dropdown — native ComboBox
                            ComboBox {
                                id: channelCombo
                                width: 117
                                height: 24
                                Layout.alignment: Qt.AlignHCenter
                                model: root.groups
                                currentIndex: {
                                    var g = (appCard.appData.group || "aux").toUpperCase()
                                    for (var i = 0; i < root.groups.length; i++) {
                                        if (root.groups[i] === g) return i
                                    }
                                    return root.groups.length - 1
                                }

                                // Don't let ComboBox handle activation — we do it in the delegate
                                onActivated: function(index) {}

                                // Display text
                                contentItem: Text {
                                    text: channelCombo.currentText
                                    font.pixelSize: 10
                                    font.bold: true
                                    color: root.accent
                                    horizontalAlignment: Text.AlignHCenter
                                    verticalAlignment: Text.AlignVCenter
                                    elide: Text.ElideRight
                                }

                                // Button background
                                background: Rectangle {
                                    color: channelCombo.popup.visible ? root.bgCardHover : "#22222e"
                                    border.width: 1
                                    border.color: channelCombo.popup.visible ? root.accent : "#33333f"
                                    radius: 4
                                    implicitHeight: 24
                                    implicitWidth: 117
                                }

                                // Popup styling
                                popup: Popup {
                                    y: channelCombo.height
                                    width: 117
                                    implicitHeight: contentItem.implicitHeight
                                    padding: 4

                                    background: Rectangle {
                                        color: root.bgCard
                                        border.width: 1
                                        border.color: root.borderColor
                                        radius: 4
                                    }

                                    contentItem: ColumnLayout {
                                        spacing: 0

                                        Repeater {
                                            model: root.groups

                                            Rectangle {
                                                Layout.fillWidth: true
                                                Layout.preferredHeight: 26
                                                radius: 3
                                                color: modelData === (appCard.appData.group || "aux").toUpperCase()
                                                    ? root.accent
                                                    : (comboMouse.containsMouse ? root.bgCardHover : "transparent")

                                                Text {
                                                    anchors.left: parent.left
                                                    anchors.leftMargin: 10
                                                    anchors.verticalCenter: parent.verticalCenter
                                                    text: modelData
                                                    font.pixelSize: 10
                                                    font.bold: true
                                                    color: modelData === (appCard.appData.group || "aux").toUpperCase()
                                                        ? "white"
                                                        : root.textColor
                                                }

                                                MouseArea {
                                                    id: comboMouse
                                                    anchors.fill: parent
                                                    hoverEnabled: true
                                                    cursorShape: Qt.PointingHandCursor
                                                    onClicked: {
                                                        var group = modelData.toLowerCase()
                                                        root.appMoved(appCard.appData.key || appCard.appData.name, group, appCard.appData.id)
                                                        channelCombo.popup.close()
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }

                        // Hover detection for card highlight
                        MouseArea {
                            id: mouseArea2
                            anchors.fill: parent
                            hoverEnabled: true
                            acceptedButtons: Qt.NoButton
                            propagateComposedEvents: true
                        }
                    }
                }
            }
        }
    }
}
