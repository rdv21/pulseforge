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
        // AFX
        var afx = s.afx || {}
        afxEnableCheckbox.checked = afx.enabled
        afxCard.sliderValue = afx.intensity || 70
        afxModeCombo.currentIndex = afxModeCombo.indexOfValue(afx.effect_mode || "denoiser")
        afxStatusText.text = afx.available ? "RTX GPU — Active" : "No RTX GPU detected"
        afxStatusText.color = afx.available ? micWindow.streamGreen : "#aa4444"
        // Compressor
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

            // ─── Monitor ───
            RowLayout {
                Layout.fillWidth: true
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

            // ═══ NVIDIA AFX ═══
            Rectangle {
                Layout.fillWidth: true
                radius: 8
                color: micWindow.bgCard
                border.width: 1
                border.color: micWindow.borderColor
                implicitHeight: afxColumn.implicitHeight + 20

                ColumnLayout {
                    id: afxColumn
                    anchors.fill: parent
                    anchors.margins: 10
                    spacing: 6

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 6

                        Text {
                            text: "⚡ NVIDIA AFX — RTX Voice"
                            font.pixelSize: 12
                            font.bold: true
                            color: micWindow.textDim
                            Layout.fillWidth: true
                        }
                        Text {
                            id: afxStatusText
                            text: "Checking..."
                            font.pixelSize: 10
                            color: micWindow.textDim
                        }
                        Rectangle {
                            id: afxEnableCheckbox
                            width: 16; height: 16
                            radius: 3
                            color: checked ? micWindow.accent : micWindow.bgDark
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
                                    if (!micWindow._loading && typeof PulseForge !== "undefined") PulseForge.setAfxEnabled(parent.checked)
                                }
                            }
                        }
                        Text {
                            text: "Enable"
                            font.pixelSize: 10
                            color: micWindow.textColor
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 6
                        Text {
                            text: "Mode:"
                            font.pixelSize: 10
                            color: micWindow.textDim
                        }
                        ComboBox {
                            id: afxModeCombo
                            Layout.fillWidth: true
                            Layout.preferredHeight: 26
                            font.pixelSize: 10
                            model: [
                                { value: "denoiser", label: "Noise Removal" },
                                { value: "denoiser_v2", label: "BNR 2.0 (Enhanced)" },
                                { value: "dereverb", label: "Room Echo Removal" },
                                { value: "dereverb_denoiser", label: "Noise + Room Echo" },
                                { value: "studio_voice_low_latency", label: "Studio Voice" }
                            ]
                            textRole: "label"
                            valueRole: "value"
                            onActivated: function(index) {
                                if (!micWindow._loading && typeof PulseForge !== "undefined") PulseForge.setAfxEffectMode(currentValue)
                            }
                            function indexOfValue(val) {
                                for (var i = 0; i < count; i++) { if (model[i].value === val) return i }
                                return 0
                            }
                        }
                    }

                    ProcessingCard {
                        id: afxCard
                        title: "AFX Intensity"
                        Layout.fillWidth: true
                        sliderLabel: "Intensity"
                        sliderValue: 70
                        sliderUnit: "%"
                        sliderMin: 0
                        sliderMax: 100
                        onSliderMoved: function(value) {
                            if (!micWindow._loading && typeof PulseForge !== "undefined") PulseForge.setAfxIntensity(value / 100.0)
                        }
                    }
                }
            }

            // ═══ Gate + Compressor (side by side, equal height) ═══
            RowLayout {
                Layout.fillWidth: true
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
