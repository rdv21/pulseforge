import QtQuick
import QtQuick.Controls

Canvas {
    id: vu
    width: 22
    height: 140

    // ─── Props ───
    property real peak: 0.0
    property real targetPeak: 0.0
    property real holdPeak: 0.0
    property int holdTimer: 0
    property bool clip: false
    property bool mockAnimation: true  // Set false for real data

    // ─── Tintable colors ───
    property color tintGreen: "#4caf50"
    property color tintYellow: "#d4a824"
    property color tintRed: "#e04040"

    readonly property color bg: "#0e0e16"
    readonly property color holdColor: "#ffffff"

    // ─── Animation Timer ───
    Timer {
        interval: 33
        running: true
        repeat: true
        onTriggered: {
            var diff = vu.targetPeak - vu.peak;
            if (Math.abs(diff) > 0.001) {
                if (diff > 0) vu.peak += diff * 0.5;
                else vu.peak += diff * 0.15;
            } else {
                vu.peak = vu.targetPeak;
            }

            if (vu.peak > vu.holdPeak) {
                vu.holdPeak = vu.peak;
                vu.holdTimer = 0;
            } else {
                vu.holdTimer++;
                if (vu.holdTimer > 45) {
                    vu.holdPeak = Math.max(0.0, vu.holdPeak - 0.02);
                }
            }

            if (vu.peak < 0.95) vu.clip = false;
            vu.requestPaint();
        }
    }

    // Mock animation for prototype
    Timer {
        interval: 80
        running: vu.mockAnimation
        repeat: true
        property real phase: Math.random() * 6.28
        onTriggered: {
            var t = Date.now() / 1000 + phase;
            var base = 0.3 + 0.25 * Math.sin(t * 2.3) + 0.15 * Math.sin(t * 5.7);
            var noise = Math.random() * 0.2;
            vu.targetPeak = Math.max(0.02, Math.min(1.05, base + noise));
            if (vu.targetPeak >= 1.0) vu.clip = true;
        }
    }

    onWidthChanged: requestPaint()
    onHeightChanged: requestPaint()
    onTintGreenChanged: requestPaint()
    onTintYellowChanged: requestPaint()
    onTintRedChanged: requestPaint()

    onPaint: {
        var ctx = getContext("2d");
        ctx.reset();

        var w = width;
        var h = height;

        ctx.fillStyle = bg;
        ctx.fillRect(0, 0, w, h);

        var radius = 3;
        var barH = h * Math.min(peak, 1.0);

        if (barH > 2) {
            var yTop = h - barH;
            var grad = ctx.createLinearGradient(0, h, 0, 0);
            grad.addColorStop(0.0, tintGreen);
            grad.addColorStop(0.5, tintGreen);
            grad.addColorStop(0.7, tintGreen);
            grad.addColorStop(0.8, tintYellow);
            grad.addColorStop(0.95, tintRed);
            grad.addColorStop(1.0, tintRed);

            ctx.fillStyle = grad;
            ctx.beginPath();
            ctx.moveTo(0, h);
            ctx.lineTo(0, yTop + radius);
            ctx.quadraticCurveTo(0, yTop, radius, yTop);
            ctx.lineTo(w - radius, yTop);
            ctx.quadraticCurveTo(w, yTop, w, yTop + radius);
            ctx.lineTo(w, h);
            ctx.closePath();
            ctx.fill();
        }

        if (holdPeak > 0.01) {
            var holdY = h - h * Math.min(holdPeak, 1.0);
            ctx.fillStyle = holdColor;
            ctx.globalAlpha = 0.85;
            ctx.fillRect(0, holdY - 1, w, 2);
            ctx.globalAlpha = 1.0;
        }

        if (clip) {
            ctx.fillStyle = tintRed;
            ctx.fillRect(0, 0, w, 4);
        }

        ctx.strokeStyle = "#22222e";
        ctx.lineWidth = 1;
        ctx.strokeRect(0.5, 0.5, w - 1, h - 1);
    }
}