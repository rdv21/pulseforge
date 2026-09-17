import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window
import "components"

Window {
    id: sbWindow
    visible: false
    width: 560
    height: 760
    minimumWidth: 480
    minimumHeight: 680
    title: "PulseForge — Soundboard"
    color: "#0d0d14"
    flags: Qt.Window | Qt.WindowStaysOnTopHint

    readonly property color bgDark: "#0d0d14"
    readonly property color bgCard: "#16161f"
    readonly property color borderColor: "#252533"
    readonly property color accent: "#4a9eff"
    readonly property color accentDim: "#2a5a8a"
    readonly property color textColor: "#c8c8d0"
    readonly property color textDim: "#7a7a88"
    readonly property color streamGreen: "#3a8a4a"
    readonly property color recordRed: "#aa3333"
    readonly property color trimColor: "#ff8c42"
    readonly property color publishGold: "#d4a017"

    property int currentPage: 0
    property bool _loading: false
    property string selectedChannel: "game"  // for capture, not "record" toggle
    property var liveWaveform: []
    property var clipWaveform: []
    property bool hasClip: false
    property real clipDuration: 0
    property real trimStart: 0
    property real trimEnd: 0
    property bool clipPlaying: false
    property bool editorMode: false
    property string outputTarget: "pulseforge_gaming"
    property bool assignMode: false  // after publish, next slot click assigns
    property string publishedName: ""

    Component.onCompleted: {
        loadSoundboard()
        if (typeof PulseForge !== "undefined") {
            outputTarget = PulseForge.getSoundboardOutput()
            outputCombo.currentIndex = _targetToIndex(outputTarget)
        }
    }

    function _targetToIndex(target) {
        var map = {"pulseforge_gaming": 0, "pulseforge_game": 1, "pulseforge_chat": 2,
                   "pulseforge_media": 3, "pulseforge_aux": 4, "pulseforge_stream": 5}
        return map[target] !== undefined ? map[target] : 0
    }

    function _indexToTarget(idx) {
        var targets = ["pulseforge_gaming", "pulseforge_game", "pulseforge_chat",
                       "pulseforge_media", "pulseforge_aux", "pulseforge_stream"]
        return targets[idx] || "pulseforge_gaming"
    }

    // Listen for soundboard changes from backend
    Connections {
        target: PulseForge
        function onSoundboardChanged(page) {
            if (page === sbWindow.currentPage) loadSoundboard()
        }
    }

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
        _loading = false
    }

    function loadClipInfo() {
        if (typeof PulseForge === "undefined") return
        var info = PulseForge.getClipInfo()
        hasClip = info.available
        clipDuration = info.duration || 0
        trimStart = info.trim_start || 0
        trimEnd = info.trim_end || 0
        if (hasClip) {
            clipWaveform = PulseForge.getClipWaveform()
            clipEditorCanvas.requestPaint()
        }
    }

    onVisibleChanged: {
        if (visible) loadSoundboard()
        else editorMode = false
    }

    // Timer for clip playing state
    Timer {
        id: playTimer
        interval: 200
        repeat: true
        onTriggered: {
            if (typeof PulseForge !== "undefined") {
                clipPlaying = PulseForge.isClipPlaying()
                if (!clipPlaying) stop()
            }
        }
    }

    // Live waveform refresh — always running when window is visible
    Timer {
        id: liveWaveformTimer
        interval: 100
        running: sbWindow.visible && !editorMode
        repeat: true
        onTriggered: {
            if (typeof PulseForge !== "undefined") {
                liveWaveform = PulseForge.getChannelWaveform(selectedChannel)
                liveWaveformCanvas.requestPaint()
            }
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 12
        spacing: 8

        // ─── Header ───
        RowLayout {
            Layout.fillWidth: true
            spacing: 8

            Text {
                text: editorMode ? "🔊 Soundboard ← Clip Editor" : "🔊 Soundboard"
                font.pixelSize: 15
                font.bold: true
                color: sbWindow.accent
            }
            Item { Layout.fillWidth: true }

            // Editor toggle (only visible when clip exists)
            Button {
                text: editorMode ? "← Back to Grid" : "Clip Editor →"
                visible: hasClip
                flat: true
                height: 24
                contentItem: Text {
                    text: parent.text
                    font.pixelSize: 10
                    font.bold: true
                    color: parent.pressed ? sbWindow.accent : sbWindow.accentDim
                    anchors.fill: parent
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }
                background: Rectangle {
                    color: sbWindow.bgCard
                    border.color: sbWindow.borderColor
                    border.width: 1
                    radius: 5
                    implicitHeight: 24
                    implicitWidth: 90
                }
                onClicked: {
                    editorMode = !editorMode
                    if (editorMode) loadClipInfo()
                }
            }

            Button {
                text: "Close"
                flat: true
                height: 24
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
                    implicitHeight: 24
                    implicitWidth: 56
                }
                onClicked: sbWindow.hide()
            }
        }

        // ═══════════════════════════════════════════════════
        // GRID VIEW (soundboard + always-on recording)
        // ═══════════════════════════════════════════════════
        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 8
            visible: !editorMode

            // ─── Assign mode banner ───
            Rectangle {
                Layout.fillWidth: true
                height: 32
                radius: 5
                color: "#2a2a1a"
                border.width: 1
                border.color: sbWindow.publishGold
                visible: assignMode

                RowLayout {
                    anchors.fill: parent
                    anchors.margins: 8
                    spacing: 6

                    Text {
                        text: "📎 '" + publishedName + "' — click a slot to assign, or"
                        font.pixelSize: 11
                        color: sbWindow.publishGold
                        Layout.fillWidth: true
                    }

                    Button {
                        text: "Cancel"
                        flat: true
                        height: 20
                        contentItem: Text {
                            text: parent.text
                            font.pixelSize: 9
                            color: sbWindow.textDim
                            anchors.fill: parent
                            horizontalAlignment: Text.AlignHCenter
                            verticalAlignment: Text.AlignVCenter
                        }
                        background: Rectangle {
                            color: sbWindow.bgDark
                            border.color: sbWindow.borderColor
                            border.width: 1
                            radius: 3
                            implicitHeight: 20
                            implicitWidth: 50
                        }
                        onClicked: {
                            assignMode = false
                            PulseForge.clearPublished()
                        }
                    }
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
                        height: 28
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
                            onClicked: { sbWindow.currentPage = index; loadSoundboard() }
                        }
                    }
                }
            }

            // ─── Output target selector ───
            RowLayout {
                Layout.fillWidth: true
                spacing: 6

                Text {
                    text: "Output:"
                    font.pixelSize: 10
                    color: sbWindow.textDim
                }

                ComboBox {
                    id: outputCombo
                    Layout.fillWidth: true
                    model: ["Main Mix + Stream", "Game", "Chat", "Media", "Aux", "Stream Only"]
                    font.pixelSize: 10
                    onActivated: {
                        var target = _indexToTarget(currentIndex)
                        sbWindow.outputTarget = target
                        if (typeof PulseForge !== "undefined")
                            PulseForge.setSoundboardOutput(target)
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
                border.color: assignMode ? sbWindow.publishGold : sbWindow.borderColor

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
                            color: {
                                if (assignMode) return "#2a2a1a"
                                return isAssigned ? "#1a2a3a" : sbWindow.bgDark
                            }
                            border.width: assignMode ? 2 : 1
                            border.color: {
                                if (assignMode) return sbWindow.publishGold
                                return isAssigned ? sbWindow.accent : sbWindow.borderColor
                            }

                            property string slotName: ""
                            property string slotFile: ""
                            property bool isAssigned: false

                            ColumnLayout {
                                anchors.fill: parent
                                anchors.margins: 8
                                spacing: 2

                                Text {
                                    text: isAssigned ? slotName : (assignMode ? "→ Assign here" : "Empty")
                                    font.pixelSize: 11
                                    font.bold: isAssigned || assignMode
                                    color: assignMode ? sbWindow.publishGold : (isAssigned ? sbWindow.textColor : sbWindow.textDim)
                                    Layout.fillWidth: true
                                    elide: Text.ElideRight
                                    horizontalAlignment: Text.AlignHCenter
                                }

                                Text {
                                    text: isAssigned ? "▶" : (assignMode ? "📎" : "+")
                                    font.pixelSize: 24
                                    color: assignMode ? sbWindow.publishGold : (isAssigned ? sbWindow.accent : sbWindow.textDim)
                                    Layout.alignment: Qt.AlignHCenter
                                }

                                Text {
                                    text: index + 1
                                    font.pixelSize: 8
                                    color: sbWindow.textDim
                                    Layout.alignment: Qt.AlignHCenter | Qt.AlignBottom
                                }
                            }

                            MouseArea {
                                anchors.fill: parent
                                cursorShape: Qt.PointingHandCursor
                                pressAndHoldInterval: 400

                                onClicked: {
                                    if (assignMode) {
                                        // Assign published clip to this slot
                                        if (PulseForge.assignPublishedClip(sbWindow.currentPage, index)) {
                                            assignMode = false
                                            loadSoundboard()
                                        }
                                    } else if (isAssigned) {
                                        PulseForge.playSound(sbWindow.currentPage, index)
                                    }
                                }
                                onPressAndHold: {
                                    if (!assignMode) {
                                        PulseForge.openSoundFileDialog(sbWindow.currentPage, index)
                                    }
                                }
                            }
                        }
                    }
                }
            }

            // ─── Always-on recording section ───
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

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 6

                        Text {
                            text: "Live Recording (15s ring buffer — always on)"
                            font.pixelSize: 12
                            font.bold: true
                            color: sbWindow.textDim
                            Layout.fillWidth: true
                        }

                        // Always-on REC indicator
                        Rectangle {
                            width: 8
                            height: 8
                            radius: 4
                            color: sbWindow.recordRed
                            anchors.verticalCenter: parent.verticalCenter
                            visible: typeof PulseForge !== "undefined" && PulseForge.isChannelRecording(selectedChannel)

                            SequentialAnimation on opacity {
                                running: true
                                loops: Animation.Infinite
                                NumberAnimation { to: 0.3; duration: 600 }
                                NumberAnimation { to: 1.0; duration: 600 }
                            }
                        }

                        Text {
                            text: "● REC"
                            font.pixelSize: 10
                            font.bold: true
                            color: sbWindow.recordRed
                            visible: typeof PulseForge !== "undefined" && PulseForge.isChannelRecording(selectedChannel)
                        }
                    }

                    // Channel selector (which channel to monitor/capture)
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 4

                        Repeater {
                            model: ["game", "chat", "media", "aux", "mic"]

                            Rectangle {
                                Layout.fillWidth: true
                                height: 26
                                radius: 4
                                color: sbWindow.selectedChannel === modelData ? sbWindow.accentDim : sbWindow.bgDark
                                border.width: 1
                                border.color: sbWindow.selectedChannel === modelData ? sbWindow.accent : sbWindow.borderColor

                                Text {
                                    anchors.centerIn: parent
                                    text: modelData.charAt(0).toUpperCase() + modelData.slice(1)
                                    font.pixelSize: 10
                                    font.bold: sbWindow.selectedChannel === modelData
                                    color: sbWindow.selectedChannel === modelData ? "white" : sbWindow.textDim
                                }

                                MouseArea {
                                    anchors.fill: parent
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: sbWindow.selectedChannel = modelData
                                }
                            }
                        }
                    }

                    // Live waveform
                    Rectangle {
                        Layout.fillWidth: true
                        height: 70
                        radius: 4
                        color: sbWindow.bgDark
                        border.width: 1
                        border.color: sbWindow.borderColor

                        Canvas {
                            id: liveWaveformCanvas
                            anchors.fill: parent
                            anchors.margins: 2

                            onPaint: {
                                var ctx = getContext("2d")
                                ctx.fillStyle = "#0d0d14"
                                ctx.fillRect(0, 0, width, height)
                                var peaks = sbWindow.liveWaveform
                                if (peaks.length === 0) {
                                    ctx.strokeStyle = "#252533"
                                    ctx.lineWidth = 1
                                    ctx.beginPath()
                                    ctx.moveTo(0, height / 2)
                                    ctx.lineTo(width, height / 2)
                                    ctx.stroke()
                                    ctx.fillStyle = "#7a7a88"
                                    ctx.font = "10px monospace"
                                    ctx.fillText("Waiting for audio...", 10, height / 2 + 4)
                                    return
                                }
                                var barWidth = width / peaks.length
                                var mid = height / 2
                                for (var i = 0; i < peaks.length; i++) {
                                    var x = i * barWidth
                                    var peakH = peaks[i].peak * mid
                                    var rmsH = peaks[i].rms * mid
                                    ctx.fillStyle = "#2a5a8a"
                                    ctx.fillRect(x, mid - rmsH, barWidth - 1, rmsH * 2)
                                    ctx.fillStyle = "#4a9eff"
                                    ctx.fillRect(x, mid - peakH, barWidth - 1, peakH * 2)
                                }
                                ctx.strokeStyle = "#252533"
                                ctx.lineWidth = 1
                                ctx.beginPath()
                                ctx.moveTo(0, mid)
                                ctx.lineTo(width, mid)
                                ctx.stroke()
                            }
                        }
                    }

                    // Capture button
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 6

                        Button {
                            text: "Capture Clip (15s)"
                            enabled: typeof PulseForge !== "undefined" && PulseForge.isChannelRecording(selectedChannel)
                            height: 32
                            contentItem: Text {
                                text: parent.text
                                font.pixelSize: 12
                                font.bold: true
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
                                implicitHeight: 32
                                implicitWidth: 130
                            }
                            onClicked: {
                                PulseForge.captureClip(selectedChannel)
                                loadClipInfo()
                                editorMode = true
                            }
                        }

                        Item { Layout.fillWidth: true }

                        Text {
                            text: hasClip ? "Clip ready →" : ""
                            font.pixelSize: 10
                            color: sbWindow.streamGreen
                            visible: hasClip
                        }
                    }
                }
            }
        }

        // ═══════════════════════════════════════════════════
        // CLIP EDITOR VIEW
        // ═══════════════════════════════════════════════════
        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 8
            visible: editorMode

            // ─── Waveform display with trim handles ───
            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                radius: 8
                color: sbWindow.bgCard
                border.width: 1
                border.color: sbWindow.borderColor

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 10
                    spacing: 6

                    // Info bar
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8

                        Text {
                            text: "Waveform Editor"
                            font.pixelSize: 12
                            font.bold: true
                            color: sbWindow.textDim
                        }

                        Item { Layout.fillWidth: true }

                        Text {
                            text: {
                                if (!hasClip) return "No clip"
                                var ts = trimStart.toFixed(2)
                                var te = trimEnd.toFixed(2)
                                var td = (trimEnd - trimStart).toFixed(2)
                                return ts + "s — " + te + "s (" + td + "s)"
                            }
                            font.pixelSize: 10
                            color: sbWindow.textColor
                            font.family: "monospace"
                        }
                    }

                    // Waveform canvas with trim overlays
                    Item {
                        id: waveformContainer
                        Layout.fillWidth: true
                        Layout.fillHeight: true

                        Canvas {
                            id: clipEditorCanvas
                            anchors.fill: parent
                            onPaint: {
                                var ctx = getContext("2d")
                                var w = width, h = height
                                ctx.fillStyle = "#0d0d14"
                                ctx.fillRect(0, 0, w, h)

                                var peaks = sbWindow.clipWaveform
                                if (peaks.length === 0) {
                                    ctx.strokeStyle = "#252533"
                                    ctx.lineWidth = 1
                                    ctx.beginPath()
                                    ctx.moveTo(0, h / 2)
                                    ctx.lineTo(w, h / 2)
                                    ctx.stroke()
                                    ctx.fillStyle = "#7a7a88"
                                    ctx.font = "12px monospace"
                                    ctx.fillText("No clip captured", 10, h / 2 + 4)
                                    return
                                }

                                var barWidth = w / peaks.length
                                var mid = h / 2

                                for (var i = 0; i < peaks.length; i++) {
                                    var x = i * barWidth
                                    var peakH = peaks[i].peak * mid
                                    var rmsH = peaks[i].rms * mid

                                    var frac = i / peaks.length
                                    var timePos = frac * sbWindow.clipDuration
                                    var inTrim = timePos >= sbWindow.trimStart && timePos <= sbWindow.trimEnd

                                    ctx.fillStyle = inTrim ? "#2a6a4a" : "#1a2a1a"
                                    ctx.fillRect(x, mid - rmsH, barWidth - 1, rmsH * 2)

                                    ctx.fillStyle = inTrim ? "#3a8a4a" : "#2a3a2a"
                                    ctx.fillRect(x, mid - peakH, barWidth - 1, peakH * 2)
                                }

                                ctx.strokeStyle = "#252533"
                                ctx.lineWidth = 1
                                ctx.beginPath()
                                ctx.moveTo(0, mid)
                                ctx.lineTo(w, mid)
                                ctx.stroke()

                                var trimX1 = (sbWindow.trimStart / sbWindow.clipDuration) * w
                                var trimX2 = (sbWindow.trimEnd / sbWindow.clipDuration) * w

                                ctx.fillStyle = "rgba(0, 0, 0, 0.5)"
                                ctx.fillRect(0, 0, trimX1, h)
                                ctx.fillRect(trimX2, 0, w - trimX2, h)

                                ctx.strokeStyle = "#ff8c42"
                                ctx.lineWidth = 2
                                ctx.beginPath()
                                ctx.moveTo(trimX1, 0)
                                ctx.lineTo(trimX1, h)
                                ctx.moveTo(trimX2, 0)
                                ctx.lineTo(trimX2, h)
                                ctx.stroke()

                                ctx.fillStyle = "#ff8c42"
                                ctx.beginPath()
                                ctx.moveTo(trimX1 - 6, 0)
                                ctx.lineTo(trimX1 + 6, 0)
                                ctx.lineTo(trimX1, 10)
                                ctx.closePath()
                                ctx.fill()
                                ctx.beginPath()
                                ctx.moveTo(trimX2 - 6, 0)
                                ctx.lineTo(trimX2 + 6, 0)
                                ctx.lineTo(trimX2, 10)
                                ctx.closePath()
                                ctx.fill()
                            }

                            MouseArea {
                                id: trimDragArea
                                anchors.fill: parent
                                hoverEnabled: true
                                property string draggingHandle: ""

                                onPressed: function(mouse) {
                                    var w = width
                                    var trimX1 = (sbWindow.trimStart / sbWindow.clipDuration) * w
                                    var trimX2 = (sbWindow.trimEnd / sbWindow.clipDuration) * w
                                    if (Math.abs(mouse.x - trimX1) < 12) {
                                        draggingHandle = "start"
                                    } else if (Math.abs(mouse.x - trimX2) < 12) {
                                        draggingHandle = "end"
                                    } else if (mouse.x > trimX1 && mouse.x < trimX2) {
                                        PulseForge.playClipPreview()
                                        playTimer.start()
                                    }
                                }

                                onReleased: { draggingHandle = "" }

                                onPositionChanged: function(mouse) {
                                    if (draggingHandle === "") return
                                    var w = width
                                    var timePos = (mouse.x / w) * sbWindow.clipDuration
                                    timePos = Math.max(0, Math.min(sbWindow.clipDuration, timePos))
                                    if (draggingHandle === "start") {
                                        sbWindow.trimStart = Math.min(timePos, sbWindow.trimEnd - 0.05)
                                    } else if (draggingHandle === "end") {
                                        sbWindow.trimEnd = Math.max(timePos, sbWindow.trimStart + 0.05)
                                    }
                                    PulseForge.setClipTrim(sbWindow.trimStart, sbWindow.trimEnd)
                                    clipEditorCanvas.requestPaint()
                                }
                            }
                        }
                    }

                    // Time axis
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 0

                        Text {
                            text: "0.0s"
                            font.pixelSize: 8
                            color: sbWindow.textDim
                            font.family: "monospace"
                        }
                        Item { Layout.fillWidth: true }
                        Text {
                            text: (clipDuration / 2).toFixed(1) + "s"
                            font.pixelSize: 8
                            color: sbWindow.textDim
                            font.family: "monospace"
                        }
                        Item { Layout.fillWidth: true }
                        Text {
                            text: clipDuration.toFixed(1) + "s"
                            font.pixelSize: 8
                            color: sbWindow.textDim
                            font.family: "monospace"
                        }
                    }

                    // ─── Trim sliders ───
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8

                        Text {
                            text: "Start"
                            font.pixelSize: 10
                            color: sbWindow.textDim
                        }

                        Slider {
                            id: trimStartSlider
                            Layout.fillWidth: true
                            from: 0
                            to: clipDuration
                            value: trimStart
                            onValueChanged: {
                                if (trimStartSlider.pressed || trimStartSlider.activeFocus) {
                                    sbWindow.trimStart = Math.min(value, sbWindow.trimEnd - 0.05)
                                    PulseForge.setClipTrim(sbWindow.trimStart, sbWindow.trimEnd)
                                    clipEditorCanvas.requestPaint()
                                }
                            }
                            Behavior on value { NumberAnimation { duration: 50 } }
                        }

                        Text {
                            text: trimStart.toFixed(2) + "s"
                            font.pixelSize: 10
                            color: sbWindow.trimColor
                            font.family: "monospace"
                            Layout.preferredWidth: 50
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8

                        Text {
                            text: "End"
                            font.pixelSize: 10
                            color: sbWindow.textDim
                        }

                        Slider {
                            id: trimEndSlider
                            Layout.fillWidth: true
                            from: 0
                            to: clipDuration
                            value: trimEnd
                            onValueChanged: {
                                if (trimEndSlider.pressed || trimEndSlider.activeFocus) {
                                    sbWindow.trimEnd = Math.max(value, sbWindow.trimStart + 0.05)
                                    PulseForge.setClipTrim(sbWindow.trimStart, sbWindow.trimEnd)
                                    clipEditorCanvas.requestPaint()
                                }
                            }
                            Behavior on value { NumberAnimation { duration: 50 } }
                        }

                        Text {
                            text: trimEnd.toFixed(2) + "s"
                            font.pixelSize: 10
                            color: sbWindow.trimColor
                            font.family: "monospace"
                            Layout.preferredWidth: 50
                        }
                    }

                    // ─── Playback + Publish controls ───
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 6

                        Button {
                            text: clipPlaying ? "⏹ Stop" : "▶ Play"
                            enabled: hasClip
                            height: 30
                            contentItem: Text {
                                text: parent.text
                                font.pixelSize: 11
                                font.bold: true
                                color: parent.enabled ? (clipPlaying ? sbWindow.recordRed : sbWindow.streamGreen) : sbWindow.textDim
                                anchors.fill: parent
                                horizontalAlignment: Text.AlignHCenter
                                verticalAlignment: Text.AlignVCenter
                            }
                            background: Rectangle {
                                color: sbWindow.bgDark
                                border.color: sbWindow.borderColor
                                border.width: 1
                                radius: 5
                                implicitHeight: 30
                                implicitWidth: 80
                            }
                            onClicked: {
                                if (clipPlaying) {
                                    PulseForge.stopClipPreview()
                                    clipPlaying = false
                                } else {
                                    PulseForge.playClipPreview()
                                    clipPlaying = true
                                    playTimer.start()
                                }
                            }
                        }

                        Button {
                            text: "↩ Reset Trim"
                            enabled: hasClip
                            height: 30
                            contentItem: Text {
                                text: parent.text
                                font.pixelSize: 11
                                color: parent.enabled ? sbWindow.textColor : sbWindow.textDim
                                anchors.fill: parent
                                horizontalAlignment: Text.AlignHCenter
                                verticalAlignment: Text.AlignVCenter
                            }
                            background: Rectangle {
                                color: sbWindow.bgDark
                                border.color: sbWindow.borderColor
                                border.width: 1
                                radius: 5
                                implicitHeight: 30
                                implicitWidth: 80
                            }
                            onClicked: {
                                sbWindow.trimStart = 0
                                sbWindow.trimEnd = clipDuration
                                PulseForge.setClipTrim(0, clipDuration)
                                clipEditorCanvas.requestPaint()
                            }
                        }

                        Item { Layout.fillWidth: true }

                        // ─── Publish button ───
                        Button {
                            text: "★ Publish MP3"
                            enabled: hasClip
                            height: 30
                            contentItem: Text {
                                text: parent.text
                                font.pixelSize: 11
                                font.bold: true
                                color: parent.enabled ? sbWindow.publishGold : sbWindow.textDim
                                anchors.fill: parent
                                horizontalAlignment: Text.AlignHCenter
                                verticalAlignment: Text.AlignVCenter
                            }
                            background: Rectangle {
                                color: "#1a1a10"
                                border.color: parent.enabled ? sbWindow.publishGold : sbWindow.borderColor
                                border.width: 1
                                radius: 5
                                implicitHeight: 30
                                implicitWidth: 110
                            }
                            onClicked: {
                                var path = PulseForge.publishClip()
                                if (path && path.length > 0) {
                                    var info = PulseForge.getLastPublished()
                                    publishedName = info.name
                                    assignMode = true
                                    editorMode = false
                                    // User can now click a slot to assign, or publish again
                                }
                            }
                        }

                        Button {
                            text: "Discard"
                            enabled: hasClip
                            height: 30
                            contentItem: Text {
                                text: parent.text
                                font.pixelSize: 11
                                color: parent.enabled ? "#aa4444" : sbWindow.textDim
                                anchors.fill: parent
                                horizontalAlignment: Text.AlignHCenter
                                verticalAlignment: Text.AlignVCenter
                            }
                            background: Rectangle {
                                color: sbWindow.bgDark
                                border.color: sbWindow.borderColor
                                border.width: 1
                                radius: 5
                                implicitHeight: 30
                                implicitWidth: 70
                            }
                            onClicked: {
                                PulseForge.clearClip()
                                hasClip = false
                                clipWaveform = []
                                editorMode = false
                                clipEditorCanvas.requestPaint()
                            }
                        }
                    }

                    // Hint text
                    Text {
                        text: "Drag orange handles to trim · Click waveform to play · Publish saves MP3 to ~/Music/Soundboard REC/"
                        font.pixelSize: 8
                        color: sbWindow.textDim
                        Layout.alignment: Qt.AlignHCenter
                    }
                }
            }
        }
    }
}
