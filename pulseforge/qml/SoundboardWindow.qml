import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window
import "components"

Window {
    id: sbWindow
    visible: false
    width: 540
    height: 680
    minimumWidth: 480
    minimumHeight: 580
    title: "PulseForge — Soundboard"
    color: "#0d0d14"
    flags: Qt.Window | Qt.WindowStaysOnTopHint

    readonly property color bgDark: "#0d0d14"
    readonly property color bgCard: "#16161f"
    readonly property color borderColor: "#252533"
    readonly property color accent: "#4a9eff"
    readonly property color textColor: "#c8c8d0"
    readonly property color textDim: "#7a7a88"
    readonly property color streamGreen: "#3a8a4a"
    readonly property color recordRed: "#aa3333"

    property int currentPage: 0
    property bool _loading: false
    property string recordChannel: ""  // currently selected recording channel
    property var clipWaveform: []  // waveform data for current clip

    Component.onCompleted: loadSoundboard()

    function loadSoundboard() {
        if (typeof PulseForge === "undefined") return
        _loading = true
        var slots = PulseForge.getSoundboardSlots(currentPage)
        for (var i = 0; i < 9 && i < slots.length; i++) {
            var btn = gridRepeater.itemAt(i)
            if (btn) {
                btn.slotName = slots[i].name || ""
                btn.slotFile = slots[i].file_path || ""
                btn.isAssigned = slots[i].file_path !== ""
            }
        }
        // Check recording status
        var recs = PulseForge.getRecordingChannels()
        recordChannel = recs.length > 0 ? recs[0] : ""
        _loading = false
    }

    onVisibleChanged: { if (visible) loadSoundboard() }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 12
        spacing: 8

        // ─── Header ───
        RowLayout {
            Layout.fillWidth: true
            spacing: 8

            Text {
                text: "🔊 Soundboard"
                font.pixelSize: 15
                font.bold: true
                color: sbWindow.accent
            }
            Item { Layout.fillWidth: true }
            Button {
                text: "Close"
                flat: true
                height: 26
                contentItem: Text {
                    text: parent.text
                    font.pixelSize: 11
                    font.bold: true
                    color: parent.pressed ? sbWindow.accent : sbWindow.textDim
                    anchors.fill: parent
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }
                background: Rectangle {
                    color: parent.pressed ? "#1c1c28" : sbWindow.bgCard
                    border.color: sbWindow.borderColor
                    border.width: 1
                    radius: 5
                    implicitHeight: 26
                    implicitWidth: 64
                }
                onClicked: sbWindow.hide()
            }
        }

        // ─── Page tabs ───
        RowLayout {
            Layout.fillWidth: true
            spacing: 4

            Repeater {
                model: 3
                Rectangle {
                    Layout.fillWidth: true
                    height: 30
                    radius: 5
                    color: sbWindow.currentPage === index ? sbWindow.accent : sbWindow.bgCard
                    border.width: 1
                    border.color: sbWindow.currentPage === index ? sbWindow.accent : sbWindow.borderColor

                    Text {
                        anchors.centerIn: parent
                        text: "Page " + (index + 1)
                        font.pixelSize: 11
                        font.bold: sbWindow.currentPage === index
                        color: sbWindow.currentPage === index ? "white" : sbWindow.textDim
                    }

                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onClicked: {
                            sbWindow.currentPage = index
                            loadSoundboard()
                        }
                    }
                }
            }
        }

        // ─── 3x3 Grid ───
        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            radius: 8
            color: sbWindow.bgCard
            border.width: 1
            border.color: sbWindow.borderColor

            GridLayout {
                anchors.fill: parent
                anchors.margins: 10
                columns: 3
                rows: 3
                columnSpacing: 8
                rowSpacing: 8

                Repeater {
                    id: gridRepeater
                    model: 9

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        radius: 8
                        color: isAssigned ? "#1a2a3a" : sbWindow.bgDark
                        border.width: 1
                        border.color: isAssigned ? sbWindow.accent : sbWindow.borderColor

                        property string slotName: ""
                        property string slotFile: ""
                        property bool isAssigned: false

                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 8
                            spacing: 2

                            Text {
                                text: isAssigned ? slotName : "Empty"
                                font.pixelSize: 11
                                font.bold: isAssigned
                                color: isAssigned ? sbWindow.textColor : sbWindow.textDim
                                Layout.fillWidth: true
                                elide: Text.ElideRight
                                horizontalAlignment: Text.AlignHCenter
                            }

                            Text {
                                text: isAssigned ? "▶" : "+"
                                font.pixelSize: 24
                                color: isAssigned ? sbWindow.accent : sbWindow.textDim
                                Layout.alignment: Qt.AlignHCenter
                            }

                            Text {
                                text: index + 1
                                font.pixelSize: 8
                                color: sbWindow.textDim
                                Layout.alignment: Qt.AlignHCenter | Qt.AlignBottom
                            }
                        }

                        // Click = play, long click = file select
                        MouseArea {
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            pressAndHoldInterval: 400

                            onClicked: {
                                if (isAssigned) {
                                    PulseForge.playSound(sbWindow.currentPage, index)
                                }
                            }

                            onPressAndHold: {
                                PulseForge.openSoundFileDialog(sbWindow.currentPage, index)
                            }
                        }
                    }
                }
            }
        }

        // ─── Recording section ───
        Rectangle {
            Layout.fillWidth: true
            radius: 8
            color: sbWindow.bgCard
            border.width: 1
            border.color: sbWindow.borderColor
            implicitHeight: recordColumn.implicitHeight + 20

            ColumnLayout {
                id: recordColumn
                anchors.fill: parent
                anchors.margins: 10
                spacing: 6

                Text {
                    text: "Channel Recording (15s ring buffer)"
                    font.pixelSize: 12
                    font.bold: true
                    color: sbWindow.textDim
                    Layout.fillWidth: true
                }

                // Channel selector
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 4

                    Repeater {
                        model: ["game", "chat", "media", "aux", "mic"]

                        Rectangle {
                            Layout.fillWidth: true
                            height: 26
                            radius: 4
                            color: sbWindow.recordChannel === modelData ? sbWindow.recordRed : sbWindow.bgDark
                            border.width: 1
                            border.color: sbWindow.recordChannel === modelData ? sbWindow.recordRed : sbWindow.borderColor

                            Text {
                                anchors.centerIn: parent
                                text: modelData.charAt(0).toUpperCase() + modelData.slice(1)
                                font.pixelSize: 10
                                font.bold: sbWindow.recordChannel === modelData
                                color: sbWindow.recordChannel === modelData ? "white" : sbWindow.textDim
                            }

                            MouseArea {
                                anchors.fill: parent
                                cursorShape: Qt.PointingHandCursor
                                onClicked: {
                                    sbWindow.recordChannel = modelData
                                    if (PulseForge.isChannelRecording(modelData)) {
                                        PulseForge.stopChannelRecording(modelData)
                                    } else {
                                        PulseForge.startChannelRecording(modelData)
                                    }
                                }
                            }
                        }
                    }
                }

                // Waveform display
                Rectangle {
                    Layout.fillWidth: true
                    height: 80
                    radius: 4
                    color: sbWindow.bgDark
                    border.width: 1
                    border.color: sbWindow.borderColor

                    Canvas {
                        id: waveformCanvas
                        anchors.fill: parent
                        anchors.margins: 2

                        onPaint: {
                            var ctx = getContext("2d")
                            ctx.fillStyle = "#0d0d14"
                            ctx.fillRect(0, 0, width, height)

                            var peaks = sbWindow.clipWaveform
                            if (peaks.length === 0) {
                                ctx.strokeStyle = "#252533"
                                ctx.lineWidth = 1
                                ctx.beginPath()
                                ctx.moveTo(0, height / 2)
                                ctx.lineTo(width, height / 2)
                                ctx.stroke()
                                return
                            }

                            var barWidth = width / peaks.length
                            var midHeight = height / 2

                            for (var i = 0; i < peaks.length; i++) {
                                var peak = peaks[i].peak
                                var rms = peaks[i].rms
                                var x = i * barWidth
                                var peakH = peak * midHeight
                                var rmsH = rms * midHeight

                                // RMS (lighter)
                                ctx.fillStyle = "#2a5a8a"
                                ctx.fillRect(x, midHeight - rmsH, barWidth - 1, rmsH * 2)

                                // Peak (brighter)
                                ctx.fillStyle = "#4a9eff"
                                ctx.fillRect(x, midHeight - peakH, barWidth - 1, peakH * 2)
                            }

                            // Center line
                            ctx.strokeStyle = "#252533"
                            ctx.lineWidth = 1
                            ctx.beginPath()
                            ctx.moveTo(0, midHeight)
                            ctx.lineTo(width, midHeight)
                            ctx.stroke()
                        }
                    }

                    Timer {
                        interval: 100
                        running: sbWindow.recordChannel !== "" && sbWindow.visible
                        repeat: true
                        onTriggered: {
                            if (typeof PulseForge !== "undefined") {
                                sbWindow.clipWaveform = PulseForge.getChannelWaveform(sbWindow.recordChannel)
                                waveformCanvas.requestPaint()
                            }
                        }
                    }
                }

                // Clip controls
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 6

                    Button {
                        text: "Capture Clip"
                        enabled: sbWindow.recordChannel !== ""
                        height: 28
                        contentItem: Text {
                            text: parent.text
                            font.pixelSize: 11
                            color: parent.enabled ? sbWindow.accent : sbWindow.textDim
                            anchors.fill: parent
                            horizontalAlignment: Text.AlignHCenter
                            verticalAlignment: Text.AlignVCenter
                        }
                        background: Rectangle {
                            color: sbWindow.bgDark
                            border.color: sbWindow.borderColor
                            border.width: 1
                            radius: 5
                            implicitHeight: 28
                        }
                        onClicked: {
                            if (typeof PulseForge !== "undefined") {
                                PulseForge.captureClip(sbWindow.recordChannel)
                            }
                        }
                    }

                    Item { Layout.fillWidth: true }

                    Button {
                        text: "Export WAV"
                        enabled: sbWindow.recordChannel !== ""
                        height: 28
                        contentItem: Text {
                            text: parent.text
                            font.pixelSize: 11
                            color: parent.enabled ? sbWindow.streamGreen : sbWindow.textDim
                            anchors.fill: parent
                            horizontalAlignment: Text.AlignHCenter
                            verticalAlignment: Text.AlignVCenter
                        }
                        background: Rectangle {
                            color: sbWindow.bgDark
                            border.color: sbWindow.borderColor
                            border.width: 1
                            radius: 5
                            implicitHeight: 28
                        }
                        onClicked: {
                            if (typeof PulseForge !== "undefined") {
                                PulseForge.exportClip(sbWindow.recordChannel)
                            }
                        }
                    }
                }
            }
        }

        Item { Layout.preferredHeight: 2 }
    }
}
