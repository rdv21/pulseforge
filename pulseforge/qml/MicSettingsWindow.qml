import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window
import "components"

Window {
    id: micWindow
    visible: false
    // Dynamic height: fit content, but never exceed 90% of screen
    width: 600
    height: Math.min(860, Screen.desktopAvailableHeight * 0.9)
    minimumWidth: 540
    minimumHeight: 600
    title: "PulseForge — Mic Settings"
    color: "#0d0d14"
    flags: Qt.Window | Qt.WindowStaysOnTopHint

    property var micSpectrum: []
    property string currentPreset: ""
    property var presetList: []
    property bool _loadingPreset: false
    property bool _loading: false
    property int micTab: 0  // 0 = Basic, 1 = Advanced

    Connections {
        target: typeof PulseForge !== "undefined" ? PulseForge : null
        function onMicSpectrumUpdated(spectrum) {
            micWindow.micSpectrum = spectrum
        }
    }

    readonly property color bgDark: "#0d0d14"
    readonly property color bgCard: "#16161f"
    readonly property color bgCardHover: "#1c1c28"
    readonly property color borderColor: "#252533"
    readonly property color accent: "#4a9eff"
    readonly property color textColor: "#c8c8d0"
    readonly property color textDim: "#7a7a88"
    readonly property color streamGreen: "#3a8a4a"

    Component.onCompleted: loadSettings()

    function loadSettings() {
        if (typeof PulseForge === "undefined") return
        micWindow._loading = true
        micWindow.presetList = PulseForge.getEqPresets()
        var bands = PulseForge.getEqBands()
        eqCurve.bands = bands
        var s = PulseForge.getMicSettings()
        // Gate
        gateCard.cardEnabled = s.gate.enabled
        gateCard.sliderValue = s.gate.threshold
        // DeepVQE AI denoise
        var dv = s.deepvqe || {}
        dvEnableCheckbox.checked = dv.enabled
        dvCard.sliderValue = dv.strength || 70
        dvStatusText.text = dv.available ? "AI model loaded" : "Model unavailable"
        dvStatusText.color = dv.available ? micWindow.streamGreen : "#aa4444"
        // Chain extras (advanced)
        var hpf = s.hpf || {}
        hpfCard.cardEnabled = hpf.enabled !== false
        hpfCard.sliderValue = hpf.freq || 90
        var anr = s.ambient_nr || {}
        anrEnableCheckbox.checked = anr.enabled !== false
        anrCard.sliderValue = anr.level || 40
        var mbc = s.mbcomp || {}
        mbEnableCheckbox.checked = mbc.enabled !== false
        var lim = s.limiter || {}
        limCard.cardEnabled = lim.enabled !== false
        limCard.sliderValue = lim.ceiling || -1
        gateAutoCheckbox.checked = s.gate.auto_threshold === true        // Compressor
        compCard.cardEnabled = s.compressor.enabled
        compCard.sliderValue = s.compressor.threshold
        compCard.secondSliderValue = s.compressor.ratio
        compCard.thirdSliderValue = s.compressor.makeup
        // EQ
        eqEnabledCheckbox.checked = s.eq.enabled
        monitorToggle.checked = s.monitor
        micWindow.currentPreset = PulseForge.getCurrentPreset()
        micWindow._loading = false
    }

    onVisibleChanged: { if (visible) loadSettings() }

    ScrollView {
        anchors.fill: parent
        anchors.margins: 12
        clip: true
        contentWidth: availableWidth

        ColumnLayout {
            width: parent.width
            spacing: 8

            // ─── Header ───
            RowLayout {
                Layout.fillWidth: true
                spacing: 8

                Text {
                    text: "🎤 Mic Settings"
                    font.pixelSize: 15
                    font.bold: true
                    color: micWindow.accent
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
                        color: parent.pressed ? micWindow.accent : micWindow.textDim
                        anchors.fill: parent
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }
                    background: Rectangle {
                        color: parent.pressed ? micWindow.bgCardHover : micWindow.bgCard
                        border.color: micWindow.borderColor
                        border.width: 1
                        radius: 5
                        implicitHeight: 26
                        implicitWidth: 64
                    }
                    onClicked: micWindow.hide()
                }
            }

            // ─── Tab selector (Basic / Advanced) ───
            RowLayout {
                Layout.fillWidth: true
                spacing: 6
                Repeater {
                    model: ["Basic", "Advanced"]
                    Rectangle {
                        Layout.fillWidth: true
                        height: 28
                        radius: 6
                        color: micWindow.micTab === index ? micWindow.accent : micWindow.bgCard
                        border.width: 1
                        border.color: micWindow.micTab === index ? micWindow.accent : micWindow.borderColor
                        Text {
                            anchors.centerIn: parent
                            text: modelData
                            font.pixelSize: 11
                            font.bold: true
                            color: micWindow.micTab === index ? "white" : micWindow.textDim
                        }
                        MouseArea {
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            onClicked: micWindow.micTab = index
                        }
                    }
                }
            }

            // ─── Monitor ───
            RowLayout {
                Layout.fillWidth: true
                visible: micWindow.micTab === 0
                spacing: 8
                Text {
                    text: "Monitor (route mic to mix)"
                    font.pixelSize: 11
                    color: micWindow.textDim
                    Layout.fillWidth: true
                }
                ToggleButton {
                    id: monitorToggle
                    btnText: "Monitor"
                    preferredWidth: 72
                    onCheckedChanged: {
                        if (!micWindow._loading && typeof PulseForge !== "undefined") PulseForge.setMicMonitor(checked)
                    }
                }
            }

            // ═══ EQ ═══
            Rectangle {
                Layout.fillWidth: true
                visible: micWindow.micTab === 0
                radius: 8
                color: micWindow.bgCard
                border.width: 1
                border.color: micWindow.borderColor
                implicitHeight: eqColumn.implicitHeight + 24

                ColumnLayout {
                    id: eqColumn
                    anchors.fill: parent
                    anchors.margins: 10
                    spacing: 6

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 6

                        Text {
                            text: "Parametric EQ — 8 Band"
                            font.pixelSize: 12
                            font.bold: true
                            color: micWindow.textDim
                        }
                        Rectangle {
                            id: eqEnabledCheckbox
                            width: 16; height: 16
                            radius: 3
                            color: checked ? micWindow.accent : "transparent"
                            border.width: 1
                            border.color: checked ? micWindow.accent : micWindow.borderColor
                            property bool checked: true
                            Text {
                                anchors.centerIn: parent
                                text: "✓"
                                font.pixelSize: 11
                                font.bold: true
                                color: "white"
                                visible: parent.checked
                            }
                            MouseArea {
                                anchors.fill: parent
                                cursorShape: Qt.PointingHandCursor
                                onClicked: {
                                    parent.checked = !parent.checked
                                    if (typeof PulseForge !== "undefined") PulseForge.setEqEnabled(parent.checked)
                                }
                            }
                        }
                        Text {
                            text: "Enable"
                            font.pixelSize: 10
                            color: micWindow.textColor
                        }
                        Item { Layout.fillWidth: true }
                        Text {
                            text: "Preset:"
                            font.pixelSize: 10
                            color: micWindow.textDim
                        }
                        Rectangle {
                            id: presetBox
                            width: 140; height: 24
                            radius: 4
                            color: micWindow.bgDark
                            border.width: 1
                            border.color: presetPopup.visible ? micWindow.accent : micWindow.borderColor
                            RowLayout {
                                anchors.fill: parent
                                anchors.leftMargin: 8
                                anchors.rightMargin: 6
                                spacing: 4
                                Text {
                                    text: micWindow.currentPreset || "Select..."
                                    font.pixelSize: 10
                                    color: micWindow.textColor
                                    Layout.fillWidth: true
                                    elide: Text.ElideRight
                                }
                                Text {
                                    text: "▾"
                                    font.pixelSize: 9
                                    color: micWindow.textDim
                                }
                            }
                            MouseArea {
                                anchors.fill: parent
                                cursorShape: Qt.PointingHandCursor
                                onClicked: {
                                    if (typeof PulseForge !== "undefined") micWindow.presetList = PulseForge.getEqPresets()
                                    presetPopup.visible = !presetPopup.visible
                                }
                            }
                            Popup {
                                id: presetPopup
                                parent: presetBox
                                x: 0; y: presetBox.height + 2
                                width: presetBox.width
                                height: presetColumn.implicitHeight + 8
                                padding: 4
                                closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
                                background: Rectangle {
                                    color: micWindow.bgCard
                                    border.width: 1
                                    border.color: micWindow.borderColor
                                    radius: 4
                                }
                                contentItem: Column {
                                    id: presetColumn
                                    spacing: 0
                                    Repeater {
                                        model: micWindow.presetList
                                        Rectangle {
                                            width: presetColumn.width
                                            height: 24
                                            radius: 3
                                            color: modelData === micWindow.currentPreset ? micWindow.accent : (presetMouse.containsMouse ? micWindow.bgCardHover : "transparent")
                                            Text {
                                                anchors.left: parent.left
                                                anchors.leftMargin: 10
                                                anchors.verticalCenter: parent.verticalCenter
                                                text: modelData
                                                font.pixelSize: 10
                                                font.bold: modelData === micWindow.currentPreset
                                                color: modelData === micWindow.currentPreset ? "white" : micWindow.textColor
                                            }
                                            MouseArea {
                                                id: presetMouse
                                                anchors.fill: parent
                                                cursorShape: Qt.PointingHandCursor
                                                hoverEnabled: true
                                                onClicked: {
                                                    micWindow._loadingPreset = true
                                                    if (typeof PulseForge !== "undefined") {
                                                        PulseForge.loadEqPreset(modelData)
                                                        eqCurve.bands = PulseForge.getEqBands()
                                                    }
                                                    micWindow.currentPreset = modelData
                                                    presetPopup.visible = false
                                                    micWindow._loadingPreset = false
                                                }
                                            }
                                        }
                                    }
                                    Rectangle { width: presetColumn.width; height: 1; color: micWindow.borderColor }
                                    Rectangle {
                                        width: presetColumn.width
                                        height: 24
                                        radius: 3
                                        color: saveMouse.containsMouse ? micWindow.bgCardHover : "transparent"
                                        Text {
                                            anchors.left: parent.left
                                            anchors.leftMargin: 10
                                            anchors.verticalCenter: parent.verticalCenter
                                            text: "+ Save current as..."
                                            font.pixelSize: 10
                                            color: micWindow.accent
                                        }
                                        MouseArea {
                                            id: saveMouse
                                            anchors.fill: parent
                                            cursorShape: Qt.PointingHandCursor
                                            hoverEnabled: true
                                            onClicked: { presetPopup.visible = false; saveDialog.open() }
                                        }
                                    }
                                }
                            }
                        }
                    }

                    ParametricEQ {
                        id: eqCurve
                        Layout.fillWidth: true
                        Layout.preferredHeight: 280
                        showSpectrum: true
                        spectrum: micWindow.micSpectrum
                        onBandChanged: function(idx, freq, gain, q) {
                            if (typeof PulseForge !== "undefined") PulseForge.setEqBand(idx, freq, gain, q)
                            if (!micWindow._loadingPreset) micWindow.currentPreset = micWindow.currentPreset + " *"
                        }
                    }

                    Text {
                        text: "Drag points · Scroll = Q · Double-click = reset"
                        font.pixelSize: 8
                        color: micWindow.textDim
                        Layout.alignment: Qt.AlignHCenter
                    }
                }
            }

            // ═══ AI Denoise (DeepVQE) ═══
            Rectangle {
                Layout.fillWidth: true
                visible: micWindow.micTab === 0
                radius: 8
                color: micWindow.bgCard
                border.width: 1
                border.color: micWindow.borderColor
                implicitHeight: dvColumn.implicitHeight + 20

                ColumnLayout {
                    id: dvColumn
                    anchors.fill: parent
                    anchors.margins: 10
                    spacing: 6

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 6

                        Text {
                            text: "🧠 AI Denoise — DeepVQE"
                            font.pixelSize: 12
                            font.bold: true
                            color: micWindow.textDim
                            Layout.fillWidth: true
                        }
                        Text {
                            id: dvStatusText
                            text: "Checking..."
                            font.pixelSize: 10
                            color: micWindow.textDim
                        }
                        Rectangle {
                            id: dvEnableCheckbox
                            width: 16; height: 16
                            radius: 3
                            color: checked ? micWindow.accent : micWindow.bgDark
                            border.width: 1
                            border.color: checked ? micWindow.accent : micWindow.borderColor
                            property bool checked: false
                            Text {
                                anchors.centerIn: parent
                                text: "✓"
                                font.pixelSize: 11
                                font.bold: true
                                color: "white"
                                visible: parent.checked
                            }
                            MouseArea {
                                anchors.fill: parent
                                cursorShape: Qt.PointingHandCursor
                                onClicked: {
                                    parent.checked = !parent.checked
                                    if (!micWindow._loading && typeof PulseForge !== "undefined") PulseForge.setDeepvqeEnabled(parent.checked)
                                }
                            }
                        }
                        Text {
                            text: "Enable"
                            font.pixelSize: 10
                            color: micWindow.textColor
                        }
                    }

                    ProcessingCard {
                        id: dvCard
                        title: "AI Strength"
                        Layout.fillWidth: true
                        sliderLabel: "Strength"
                        sliderValue: 70
                        sliderUnit: "%"
                        sliderMin: 0
                        sliderMax: 100
                        onSliderMoved: function(value) {
                            if (!micWindow._loading && typeof PulseForge !== "undefined") PulseForge.setDeepvqeStrength(value / 100.0)
                        }
                    }
                }
            }

            // ═══ Adaptive Ambient NR ═══
            Rectangle {
                Layout.fillWidth: true
                visible: micWindow.micTab === 1
                radius: 8
                color: micWindow.bgCard
                border.width: 1
                border.color: micWindow.borderColor
                implicitHeight: anrColumn.implicitHeight + 20
                ColumnLayout {
                    id: anrColumn
                    anchors.fill: parent
                    anchors.margins: 10
                    spacing: 6
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 6
                        Text {
                            text: "🌫 Ambient NR — Room Tone"
                            font.pixelSize: 12
                            font.bold: true
                            color: micWindow.textDim
                            Layout.fillWidth: true
                        }
                        Rectangle {
                            id: anrEnableCheckbox
                            width: 16; height: 16
                            radius: 3
                            color: checked ? micWindow.accent : micWindow.bgDark
                            border.width: 1
                            border.color: checked ? micWindow.accent : micWindow.borderColor
                            property bool checked: true
                            Text { anchors.centerIn: parent; text: "✓"; font.pixelSize: 11; font.bold: true; color: "white"; visible: parent.checked }
                            MouseArea {
                                anchors.fill: parent
                                cursorShape: Qt.PointingHandCursor
                                onClicked: {
                                    parent.checked = !parent.checked
                                    if (!micWindow._loading && typeof PulseForge !== "undefined") PulseForge.setAmbientNrEnabled(parent.checked)
                                }
                            }
                        }
                        Text { text: "Enable"; font.pixelSize: 10; color: micWindow.textColor }
                    }
                    ProcessingCard {
                        id: anrCard
                        title: "NR Amount"
                        Layout.fillWidth: true
                        sliderLabel: "Amount"
                        sliderValue: 40
                        sliderUnit: "%"
                        sliderMin: 0
                        sliderMax: 100
                        onSliderMoved: function(value) {
                            if (!micWindow._loading && typeof PulseForge !== "undefined") PulseForge.setAmbientNrLevel(value / 100.0)
                        }
                    }
                }
            }

            // ═══ High-pass + Limiter (side by side) ═══
            RowLayout {
                Layout.fillWidth: true
                visible: micWindow.micTab === 1
                spacing: 8
                Layout.minimumHeight: Math.max(hpfCard.implicitHeight, limCard.implicitHeight)

                ProcessingCard {
                    id: hpfCard
                    title: "High-Pass"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    sliderLabel: "Freq"
                    sliderValue: 90
                    sliderUnit: " Hz"
                    sliderMin: 20
                    sliderMax: 300
                    onEnabledToggled: function(enabled) {
                        if (!micWindow._loading && typeof PulseForge !== "undefined") PulseForge.setHpfEnabled(enabled)
                    }
                    onSliderMoved: function(value) {
                        if (!micWindow._loading && typeof PulseForge !== "undefined") PulseForge.setHpfFreq(value)
                    }
                }

                ProcessingCard {
                    id: limCard
                    title: "Limiter"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    sliderLabel: "Ceiling"
                    sliderValue: -1
                    sliderUnit: " dB"
                    sliderMin: -12
                    sliderMax: 0
                    onEnabledToggled: function(enabled) {
                        if (!micWindow._loading && typeof PulseForge !== "undefined") PulseForge.setLimiterEnabled(enabled)
                    }
                    onSliderMoved: function(value) {
                        if (!micWindow._loading && typeof PulseForge !== "undefined") PulseForge.setLimiterCeiling(value)
                    }
                }
            }

            // ═══ Multiband Compressor toggle ═══
            Rectangle {
                Layout.fillWidth: true
                visible: micWindow.micTab === 1
                radius: 8
                color: micWindow.bgCard
                border.width: 1
                border.color: micWindow.borderColor
                implicitHeight: 40
                RowLayout {
                    anchors.fill: parent
                    anchors.margins: 10
                    spacing: 6
                    Text {
                        text: "🎚 Multiband Compressor (4-band)"
                        font.pixelSize: 12
                        font.bold: true
                        color: micWindow.textDim
                        Layout.fillWidth: true
                    }
                    Rectangle {
                        id: mbEnableCheckbox
                        width: 16; height: 16
                        radius: 3
                        color: checked ? micWindow.accent : micWindow.bgDark
                        border.width: 1
                        border.color: checked ? micWindow.accent : micWindow.borderColor
                        property bool checked: true
                        Text { anchors.centerIn: parent; text: "✓"; font.pixelSize: 11; font.bold: true; color: "white"; visible: parent.checked }
                        MouseArea {
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            onClicked: {
                                parent.checked = !parent.checked
                                if (!micWindow._loading && typeof PulseForge !== "undefined") PulseForge.setMbCompEnabled(parent.checked)
                            }
                        }
                    }
                    Text { text: "Enable"; font.pixelSize: 10; color: micWindow.textColor }
                }
            }

            // ═══ Gate auto-threshold toggle ═══
            Rectangle {
                Layout.fillWidth: true
                visible: micWindow.micTab === 1
                radius: 8
                color: micWindow.bgCard
                border.width: 1
                border.color: micWindow.borderColor
                implicitHeight: 40
                RowLayout {
                    anchors.fill: parent
                    anchors.margins: 10
                    spacing: 6
                    Text {
                        text: "🚪 Gate Auto-Threshold (track noise floor)"
                        font.pixelSize: 12
                        font.bold: true
                        color: micWindow.textDim
                        Layout.fillWidth: true
                    }
                    Rectangle {
                        id: gateAutoCheckbox
                        width: 16; height: 16
                        radius: 3
                        color: checked ? micWindow.accent : micWindow.bgDark
                        border.width: 1
                        border.color: checked ? micWindow.accent : micWindow.borderColor
                        property bool checked: false
                        Text { anchors.centerIn: parent; text: "✓"; font.pixelSize: 11; font.bold: true; color: "white"; visible: parent.checked }
                        MouseArea {
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            onClicked: {
                                parent.checked = !parent.checked
                                if (!micWindow._loading && typeof PulseForge !== "undefined") PulseForge.setGateAutoThreshold(parent.checked)
                            }
                        }
                    }
                    Text { text: "Auto"; font.pixelSize: 10; color: micWindow.textColor }
                }
            }

            // ═══ Gate + Compressor (side by side, equal height) ═══
            RowLayout {
                Layout.fillWidth: true
                visible: micWindow.micTab === 0
                spacing: 8
                // Both cards fill to the tallest card's height
                Layout.minimumHeight: Math.max(gateCard.implicitHeight, compCard.implicitHeight)

                ProcessingCard {
                    id: gateCard
                    title: "Gate"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    sliderLabel: "Threshold"
                    sliderValue: -35
                    sliderUnit: " dB"
                    sliderMin: -80
                    sliderMax: 0
                    onEnabledToggled: function(enabled) {
                        if (!micWindow._loading && typeof PulseForge !== "undefined") PulseForge.setGateEnabled(enabled)
                    }
                    onSliderMoved: function(value) {
                        if (!micWindow._loading && typeof PulseForge !== "undefined") PulseForge.setGateThreshold(value)
                    }
                }

                ProcessingCard {
                    id: compCard
                    title: "Compressor"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    sliderLabel: "Threshold"
                    sliderUnit: " dB"
                    sliderMin: -60
                    sliderMax: 0
                    secondSliderLabel: "Ratio"
                    secondSliderUnit: ":1"
                    secondSliderMin: 1
                    secondSliderMax: 10
                    thirdSliderLabel: "Makeup"
                    thirdSliderUnit: " dB"
                    thirdSliderMin: 0
                    thirdSliderMax: 24
                    onEnabledToggled: function(enabled) {
                        if (!micWindow._loading && typeof PulseForge !== "undefined") PulseForge.setCompEnabled(enabled)
                    }
                    onSliderMoved: function(value) {
                        if (!micWindow._loading && typeof PulseForge !== "undefined") PulseForge.setCompThreshold(value)
                    }
                    onSecondSliderMoved: function(value) {
                        if (!micWindow._loading && typeof PulseForge !== "undefined") PulseForge.setCompRatio(value)
                    }
                    onThirdSliderMoved: function(value) {
                        if (!micWindow._loading && typeof PulseForge !== "undefined") PulseForge.setCompMakeup(value)
                    }
                }
            }

            Item { Layout.preferredHeight: 2 }
        }
    }

    // ─── Save preset dialog ───
    Dialog {
        id: saveDialog
        title: "Save EQ Preset"
        modal: true
        anchors.centerIn: parent
        width: 300
        contentItem: ColumnLayout {
            spacing: 10
            TextField {
                id: presetNameField
                placeholderText: "Preset name..."
                font.pixelSize: 12
                Layout.fillWidth: true
                focus: true
                onAccepted: {
                    if (text.length > 0) {
                        if (typeof PulseForge !== "undefined") {
                            PulseForge.saveEqPreset(text)
                            micWindow.presetList = PulseForge.getEqPresets()
                            micWindow.currentPreset = text
                        }
                        saveDialog.close()
                        text = ""
                    }
                }
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                Button { text: "Cancel"; onClicked: { saveDialog.close(); presetNameField.text = "" } }
                Item { Layout.fillWidth: true }
                Button {
                    text: "Save"
                    highlighted: true
                    onClicked: {
                        if (presetNameField.text.length > 0) {
                            if (typeof PulseForge !== "undefined") {
                                PulseForge.saveEqPreset(presetNameField.text)
                                micWindow.presetList = PulseForge.getEqPresets()
                                micWindow.currentPreset = presetNameField.text
                            }
                            saveDialog.close()
                            presetNameField.text = ""
                        }
                    }
                }
            }
        }
    }
}
