import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window
import "components"

ApplicationWindow {
    id: root
    visible: true
    minimumHeight: 620
    title: "PulseForge"
    color: "#0d0d14"
    flags: Qt.Window | Qt.WindowStaysOnTopHint

    // Close to tray instead of quitting
    onClosing: {
        close.accepted = false
        root.hide()
    }

    // ─── Theme ───────────────────────────────────────────
    readonly property color bgDark: "#0d0d14"
    readonly property color bgCard: "#16161f"
    readonly property color bgCardHover: "#1c1c28"
    readonly property color borderColor: "#252533"
    readonly property color accent: "#4a9eff"
    readonly property color accentDim: "#2a5a8a"
    readonly property color textColor: "#c8c8d0"
    readonly property color textDim: "#7a7a88"
    readonly property color muteRed: "#c04040"
    readonly property color streamGreen: "#3a8a4a"

    // ─── Layout constants ────────────────────────────────
    readonly property int stripWidth: 195
    readonly property int stripSpacing: 6
    readonly property int layoutMargin: 14
    readonly property int numStrips: 6

    readonly property int minContentWidth: numStrips * stripWidth + (numStrips - 1) * stripSpacing
    readonly property int minWindowWidth: minContentWidth + layoutMargin * 2 + 4

    width: minWindowWidth
    height: 660
    minimumWidth: minWindowWidth

    // ─── Data from backend ────────────────────────────────
    property var channelGroups: ["game", "chat", "media", "aux"]
    property var apps: []
    property var outputDevices: []
    property var inputDevices: []
    property var streamDevices: []

    // VU data (pushed from backend)
    property real masterVu: 0.0
    property real masterStreamVu: 0.0
    property var channelVus: ({"game": [0,0], "chat": [0,0], "media": [0,0], "aux": [0,0]})
    property real micVu: 0.0
    property real streamVu: 0.0

    // ─── Connections to backend ───────────────────────────
    Connections {
        target: PulseForge

        function onVuUpdated(mainVu, streamVu) {
            root.masterVu = mainVu
            root.masterStreamVu = streamVu
        }

        function onChannelVuUpdated(channel, mainVu, streamVu) {
            var vus = root.channelVus
            vus[channel] = [mainVu, streamVu]
            root.channelVus = vus
            // Force re-evaluation by toggling
            channelVusChanged()
        }

        function onMicVuUpdated(vu) {
            root.micVu = vu
        }

        function onStreamVuUpdated(vu) {
            root.streamVu = vu
        }

        function onAppsChanged() {
            refreshApps()
        }

        function onDevicesChanged() {
            refreshDevices()
        }

        function onFaderSynced(channel, volume) {
            // Update the channel strip fader to match system volume
            for (var i = 0; i < channelRepeater.count; i++) {
                var item = channelRepeater.itemAt(i)
                if (item && item.channelName === channel) {
                    item.setExternalVolume(volume)
                    break
                }
            }
        }
    }

    function refreshApps() {
        if (typeof PulseForge !== "undefined" && PulseForge.getApps) {
            root.apps = PulseForge.getApps()
        }
    }

    function refreshDevices() {
        if (typeof PulseForge !== "undefined" && PulseForge.getOutputDevices) {
            root.outputDevices = PulseForge.getOutputDevices()
            root.inputDevices = PulseForge.getInputDevices()
            root.streamDevices = PulseForge.getStreamDevices()
        }
    }

    Component.onCompleted: {
        refreshApps()
        refreshDevices()
        // Restore channel + mic states after UI is created
        Qt.callLater(restoreStates)
    }

    function restoreStates() {
        // Restore each channel strip state
        for (var i = 0; i < root.channelGroups.length; i++) {
            var g = root.channelGroups[i]
            var s = PulseForge.getChannelState(g)
            // Find the channel strip in the repeater
            for (var j = 0; j < channelRepeater.count; j++) {
                var item = channelRepeater.itemAt(j)
                if (item && item.name === g.toUpperCase()) {
                    item.mainFaderValue = s.volume
                    item.muteChecked = s.mute
                    item.streamChecked = s.stream_enabled
                    item.streamFaderValue = s.stream_volume
                    break
                }
            }
        }
        // Restore mic strip state
        var ms = PulseForge.getMicState()
        if (micStrip) {
            micStrip.mainFaderValue = ms.volume
            micStrip.muteChecked = ms.mute
            micStrip.streamChecked = ms.stream_enabled
        }
    }

    // ─── Main Layout ─────────────────────────────────────
    ColumnLayout {
        anchors.fill: parent
        anchors.margins: layoutMargin
        spacing: 8

        // ─── Header ───
        RowLayout {
            Layout.fillWidth: true
            spacing: 12

            Image {
                source: Qt.resolvedUrl("pulseforge-icon.png")
                sourceSize.width: 24
                sourceSize.height: 24
                fillMode: Image.PreserveAspectFit
            }

            Text {
                text: "PulseForge"
                font.pixelSize: 16
                font.bold: true
                color: root.accent
            }

            Item { Layout.fillWidth: true }

            // Output selector
            DeviceSelector {
                label: "Out"
                model: root.outputDevices.map(d => d.name)
                selected: {
                    // Use saved device from config
                    for (var i = 0; i < root.outputDevices.length; i++) {
                        if (root.outputDevices[i].is_default) return root.outputDevices[i].name
                    }
                    return root.outputDevices.length > 0 ? root.outputDevices[0].name : ""
                }
                onDeviceSelected: function(name) {
                    for (var i = 0; i < root.outputDevices.length; i++) {
                        if (root.outputDevices[i].name === name) {
                            PulseForge.setOutputDevice(root.outputDevices[i].id, root.outputDevices[i].internal)
                            break
                        }
                    }
                }
            }

            // Input selector
            DeviceSelector {
                label: "In"
                model: root.inputDevices.map(d => d.name)
                selected: {
                    for (var i = 0; i < root.inputDevices.length; i++) {
                        if (root.inputDevices[i].is_default) return root.inputDevices[i].name
                    }
                    return root.inputDevices.length > 0 ? root.inputDevices[0].name : ""
                }
                onDeviceSelected: function(name) {
                    for (var i = 0; i < root.inputDevices.length; i++) {
                        if (root.inputDevices[i].name === name) {
                            PulseForge.setInputDevice(root.inputDevices[i].id, root.inputDevices[i].internal)
                            break
                        }
                    }
                }
            }

            // Stream selector
            DeviceSelector {
                label: "Stream"
                model: root.streamDevices.map(d => d.name)
                selected: {
                    for (var i = 0; i < root.streamDevices.length; i++) {
                        if (root.streamDevices[i].is_default) return root.streamDevices[i].name
                    }
                    return "None"
                }
                onDeviceSelected: function(name) {
                    for (var i = 0; i < root.streamDevices.length; i++) {
                        if (root.streamDevices[i].name === name) {
                            PulseForge.setStreamDevice(root.streamDevices[i].id, root.streamDevices[i].internal)
                            break
                        }
                    }
                }
            }
        }

        // Separator
        Rectangle { Layout.fillWidth: true; height: 1; color: root.borderColor }

        // ─── Channel Strips Row ───
        RowLayout {
            Layout.fillWidth: true
            Layout.preferredHeight: 320
            Layout.minimumHeight: 280
            Layout.maximumHeight: 380
            spacing: stripSpacing

            // Master strip
            ChannelStrip {
                name: "MASTER"
                isMaster: true
                showStreamFader: false
                showMuteSolo: false
                showApps: false
                mainVuValue: root.masterVu
                streamVuValue: root.masterStreamVu
                Layout.fillHeight: true
                Layout.preferredWidth: root.stripWidth
                controlWidth: root.stripWidth
            }

            // Group strips
            Repeater {
                id: channelRepeater
                model: root.channelGroups
                ChannelStrip {
                    name: modelData.toUpperCase()
                    isMaster: false
                    showStreamFader: true
                    showMuteSolo: true
                    showApps: true
                    appsText: {
                        var apps = root.apps.filter(a => a.group === modelData);
                        return apps.map(a => a.name).join(", ");
                    }
                    mainVuValue: (root.channelVus[modelData] || [0,0])[0]
                    streamVuValue: (root.channelVus[modelData] || [0,0])[1]
                    Layout.fillHeight: true
                    Layout.preferredWidth: root.stripWidth
                    controlWidth: root.stripWidth

                    onMuteToggled: function(muted) {
                        PulseForge.setChannelMute(modelData, muted)
                    }
                    onStreamToggled: function(enabled) {
                        PulseForge.setChannelStream(modelData, enabled)
                    }
                    onVolChanged: function(vol) {
                        PulseForge.setChannelVolume(modelData, vol)
                    }
                    onStreamVolChanged: function(vol) {
                        PulseForge.setChannelStreamVolume(modelData, vol)
                    }
                }
            }

            // Mic strip
            ChannelStrip {
                id: micStrip
                name: "MIC"
                isMaster: false
                isMic: true
                showStreamFader: true
                showMuteSolo: true
                showApps: false
                mainVuValue: root.micVu
                streamVuValue: 0
                Layout.fillHeight: true
                Layout.preferredWidth: root.stripWidth
                controlWidth: root.stripWidth

                onMuteToggled: function(muted) {
                    PulseForge.setMicMute(muted)
                }
                onVolChanged: function(vol) {
                    PulseForge.setMicVolume(vol)
                }
                onStreamToggled: function(enabled) {
                    PulseForge.setMicStream(enabled)
                }
                onSettingsClicked: {
                    micSettingsWindow.show()
                }
            }
        }

        // Separator
        Rectangle { Layout.fillWidth: true; height: 1; color: root.borderColor }

        // ─── App Routing Panel ───
        AppRoutingPanel {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumHeight: 100
            apps: root.apps

            onAppMoved: function(appName, group, sinkInputId) {
                PulseForge.moveAppToGroup(appName, group, sinkInputId)
            }
        }
    }

    // ─── Mic Settings Window ───
    MicSettingsWindow {
        id: micSettingsWindow
    }
}