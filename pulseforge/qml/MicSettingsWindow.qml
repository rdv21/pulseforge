import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window
import "components"

Window {
    id: micWindow
    visible: false
    width: 620
    height: 920
    minimumWidth: 560
    minimumHeight: 860
    title: "PulseForge — Mic Settings"
    color: "#0d0d14"
    flags: Qt.Window | Qt.WindowStaysOnTopHint

    // Spectrum data from backend
    property var micSpectrum: []
    property string currentPreset: ""
    property var presetList: []
    property bool _loadingPreset: false
    property bool _loading: false

    // Connection to backend for spectrum updates
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
    readonly property color accentDim: "#2a5a8a"
    readonly property color textColor: "#c8c8d0"
    readonly property color textDim: "#7a7a88"
    readonly property color streamGreen: "#3a8a4a"

    Component.onCompleted: {
        loadSettings()
    }

    function loadSettings() {
        if (typeof PulseForge === "undefined") {
            console.log("MicSettings: PulseForge not ready, will retry on show")
            return
        }
        console.log("MicSettings: loadSettings() called")
        micWindow._loading = true
        // Load preset list
        micWindow.presetList = PulseForge.getEqPresets()
        // Load current EQ bands into the visualizer
        var bands = PulseForge.getEqBands()
        eqCurve.bands = bands
        // Load all mic settings from config
        var s = PulseForge.getMicSettings()
        PulseForge.logMessage("MicSettings: noise=" + s.noise.intensity + " gate=" + s.gate.threshold + " comp=" + s.compressor.threshold + " compRatio=" + s.compressor.ratio + " compMakeup=" + s.compressor.makeup)
        // Gate
        gateCard.cardEnabled = s.gate.enabled
        gateCard.sliderValue = s.gate.threshold
        // Noise
        noiseCard.cardEnabled = s.noise.enabled
        noiseCard.sliderValue = s.noise.intensity
        // Compressor
        compCard.cardEnabled = s.compressor.enabled
        compCard.sliderValue = s.compressor.threshold
        compCard.secondSliderValue = s.compressor.ratio
        compCard.thirdSliderValue = s.compressor.makeup
        // EQ enable checkbox
        eqEnabledCheckbox.checked = s.eq.enabled
        // Monitor
        monitorToggle.checked = s.monitor
        // Just set preset label to matched preset
        micWindow.currentPreset = PulseForge.getCurrentPreset()
        micWindow._loading = false
        console.log("MicSettings: loaded from config - noise=" + s.noise.intensity + " gate=" + s.gate.threshold + " comp=" + s.compressor.threshold + " compRatio=" + s.compressor.ratio + " compMakeup=" + s.compressor.makeup)
    }

    onVisibleChanged: {
        if (visible) {
            loadSettings()
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 14
        spacing: 10

        // ─── Header ───
        RowLayout {
            Layout.fillWidth: true
            spacing: 8

            Text {
                text: "🎤 Microphone Settings"
                font.pixelSize: 16
                font.bold: true
                color: micWindow.accent
            }

            Item { Layout.fillWidth: true }

            Button {
                text: "Close"
                flat: true
                height: 28
                width: 70

                contentItem: Text {
                    text: parent.text
                    font.pixelSize: 11
                    font.bold: true
                    color: parent.pressed ? micWindow.accent : micWindow.textDim
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                    anchors.fill: parent
                }

                background: Rectangle {
                    color: parent.pressed ? micWindow.bgCardHover : micWindow.bgCard
                    border.color: micWindow.borderColor
                    border.width: 1
                    radius: 5
                    implicitHeight: 28
                    implicitWidth: 70
                }

                onClicked: micWindow.hide()
            }
        }

        // ─── Monitor toggle ───
        RowLayout {
            Layout.fillWidth: true
            spacing: 8

            Text {
                text: "Route mic to mix for monitoring"
                font.pixelSize: 11
                color: micWindow.textDim
                Layout.fillWidth: true
            }

            ToggleButton {
                id: monitorToggle
                btnText: "Monitor"
                preferredWidth: 80
                onCheckedChanged: {
                    if (!micWindow._loading && typeof PulseForge !== "undefined") PulseForge.setMicMonitor(checked)
                }
            }
        }

        Rectangle { Layout.fillWidth: true; height: 1; color: micWindow.borderColor }

        // ─── Parametric EQ (visual) ───
        Rectangle {
            Layout.fillWidth: true
            radius: 8
            color: micWindow.bgCard
            border.width: 1
            border.color: micWindow.borderColor
            implicitHeight: eqColumn.implicitHeight + 28

            ColumnLayout {
                id: eqColumn
                anchors.fill: parent
                anchors.margins: 12
                spacing: 8

                // EQ header with preset dropdown
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8

                    Text {
                        text: "Parametric EQ — 8 Band"
                        font.pixelSize: 13
                        font.bold: true
                        color: micWindow.textDim
                    }

                    Rectangle {
                        id: eqEnabledCheckbox
                        width: 18; height: 18
                        radius: 3
                        color: checked ? micWindow.accent : "transparent"
                        border.width: 1
                        border.color: checked ? micWindow.accent : micWindow.borderColor
                        property bool checked: true

                        Text {
                            anchors.centerIn: parent
                            text: "✓"
                            font.pixelSize: 12
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
                        font.pixelSize: 11
                        color: micWindow.textColor
                    }

                    Item { Layout.fillWidth: true }

                    // Preset dropdown
                    Text {
                        text: "Preset:"
                        font.pixelSize: 11
                        color: micWindow.textDim
                    }

                    Rectangle {
                        id: presetBox
                        width: 160; height: 26
                        radius: 4
                        color: micWindow.bgDark
                        border.width: 1
                        border.color: presetPopup.visible ? micWindow.accent : micWindow.borderColor
                        property bool showDropdown: false

                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: 8
                            anchors.rightMargin: 6
                            spacing: 4

                            Text {
                                text: micWindow.currentPreset || "Select..."
                                font.pixelSize: 11
                                color: micWindow.textColor
                                Layout.fillWidth: true
                                elide: Text.ElideRight
                            }

                            Text {
                                text: "▾"
                                font.pixelSize: 10
                                color: micWindow.textDim
                            }
                        }

                        MouseArea {
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            onClicked: {
                                if (typeof PulseForge !== "undefined") {
                                    micWindow.presetList = PulseForge.getEqPresets()
                                }
                                presetPopup.visible = !presetPopup.visible
                            }
                        }

                        Popup {
                            id: presetPopup
                            parent: presetBox
                            x: 0
                            y: presetBox.height + 2
                            width: presetBox.width
                            height: presetColumn.implicitHeight + 8
                            padding: 4
                            modal: false
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
                                        height: 26
                                        radius: 3
                                        color: modelData === micWindow.currentPreset
                                            ? micWindow.accent
                                            : (presetMouse.containsMouse ? micWindow.bgCardHover : "transparent")

                                        Text {
                                            anchors.left: parent.left
                                            anchors.leftMargin: 10
                                            anchors.verticalCenter: parent.verticalCenter
                                            text: modelData
                                            font.pixelSize: 10
                                            font.bold: modelData === micWindow.currentPreset
                                            color: modelData === micWindow.currentPreset
                                                ? "white"
                                                : micWindow.textColor
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
                                                    var bands = PulseForge.getEqBands()
                                                    eqCurve.bands = bands
                                                }
                                                micWindow.currentPreset = modelData
                                                presetPopup.visible = false
                                                micWindow._loadingPreset = false
                                            }
                                        }
                                    }
                                }

                                // Separator + Save new preset
                                Rectangle {
                                    width: presetColumn.width
                                    height: 1
                                    color: micWindow.borderColor
                                }

                                Rectangle {
                                    width: presetColumn.width
                                    height: 26
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
                                        onClicked: {
                                            presetPopup.visible = false
                                            saveDialog.open()
                                        }
                                    }
                                }
                            }
                        }
                    }
                }

                ParametricEQ {
                    id: eqCurve
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Layout.preferredHeight: 400
                    showSpectrum: true
                    spectrum: micWindow.micSpectrum

                    onBandChanged: function(idx, freq, gain, q) {
                        if (typeof PulseForge !== "undefined") PulseForge.setEqBand(idx, freq, gain, q)
                        // Mark preset as modified
                        if (!micWindow._loadingPreset) {
                            micWindow.currentPreset = micWindow.currentPreset + " *" 
                        }
                    }
                }

                Text {
                    text: "Drag points to adjust gain · Scroll over a point to change Q · Double-click point to reset"
                    font.pixelSize: 9
                    color: micWindow.textDim
                    Layout.alignment: Qt.AlignHCenter
                }
            }
        }

        Rectangle { Layout.fillWidth: true; height: 1; color: micWindow.borderColor }

        // ─── Processing cards: Noise + Gate ───
        RowLayout {
            Layout.fillWidth: true
            spacing: 8

            ProcessingCard {
                id: noiseCard
                title: "Noise Cancellation"
                Layout.fillWidth: true
                sliderLabel: "Intensity"
                sliderValue: 50
                sliderUnit: "%"
                sliderMin: 0
                sliderMax: 100
                onEnabledToggled: function(enabled) {
                    if (!micWindow._loading && typeof PulseForge !== "undefined") PulseForge.setNoiseEnabled(enabled)
                }
                onSliderMoved: function(value) {
                    if (!micWindow._loading && typeof PulseForge !== "undefined") PulseForge.setNoiseIntensity(value / 100.0)
                }
            }

            ProcessingCard {
                id: gateCard
                title: "Gate (Pre)"
                Layout.fillWidth: true
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
        }

        // ─── Processing card: Compressor ───
        ProcessingCard {
            id: compCard
            title: "Compressor"
            Layout.fillWidth: true
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

        Item { Layout.preferredHeight: 0 }
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

                Button {
                    text: "Cancel"
                    onClicked: { saveDialog.close(); presetNameField.text = "" }
                }

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