import QtQuick
import QtQuick.Controls

Canvas {
    id: eq
    width: 520
    height: 240

    // ─── Theme ───
    readonly property color bg: "#0e0e16"
    readonly property color gridColor: "#1a1a28"
    readonly property color gridMajor: "#252538"
    readonly property color textColor: "#5a5a6a"
    readonly property color curveColor: "#4a9eff"
    readonly property color curveFill: "#4a9eff"
    readonly property color spectrumColor: "#3a8a4a"
    readonly property color spectrumFill: "#2a5a3a"
    readonly property color spectrumVoiceColor: "#5acc6a"   // brighter green for voice
    readonly property color spectrumVoiceFill: "#3a8a4a"
    readonly property color pointColor: "#6ab4ff"
    readonly property color pointBorder: "#0d0d14"
    readonly property color pointActive: "#ffffff"
    readonly property color clipColor: "#ff3a3a"
    readonly property color vocalRangeColor: "#4a9eff"      // blue tint for vocal range band

    // ─── Signal: emitted when a band changes ───
    signal bandChanged(int idx, real freq, real gain, real q)

    // ─── EQ Bands ───
    property var bands: [
        { freq: 60,    gain: 0, q: 1.0 },
        { freq: 120,   gain: 0, q: 1.0 },
        { freq: 250,   gain: 0, q: 1.0 },
        { freq: 500,   gain: 0, q: 1.0 },
        { freq: 1000,  gain: 0, q: 1.0 },
        { freq: 2000,  gain: 0, q: 1.0 },
        { freq: 4000,  gain: 0, q: 1.0 },
        { freq: 8000,  gain: 0, q: 1.0 }
    ]

    // ─── Mic spectrum (real data from backend FFT) ───
    // Array of {freq, level} points; level 0.0..1.0 (fixed -50dB..+6dB scale)
    property var spectrum: []
    property bool showSpectrum: true

    // ─── Smoothed spectrum for animation ───
    property var _smoothedSpectrum: []
    property real smoothFactor: 0.7  // higher = slower fade

    // ─── Voice detection state ───
    property bool _voiceActive: false

    onSpectrumChanged: {
        _smoothSpectrum();
        _detectVoice();
        requestPaint();
    }

    function _smoothSpectrum() {
        if (spectrum.length === 0) {
            _smoothedSpectrum = [];
            return;
        }
        if (_smoothedSpectrum.length !== spectrum.length) {
            _smoothedSpectrum = spectrum.map(function(p) {
                return { freq: p.freq, level: p.level };
            });
            return;
        }
        var alpha = 1.0 - smoothFactor;
        for (var i = 0; i < spectrum.length; i++) {
            _smoothedSpectrum[i].level = _smoothedSpectrum[i].level * smoothFactor + spectrum[i].level * alpha;
        }
    }

    // Detect if current spectrum energy is concentrated in vocal range (85Hz-4kHz)
    // vs. spread across all frequencies (noise)
    function _detectVoice() {
        if (spectrum.length < 2) {
            _voiceActive = false;
            return;
        }
        var vocalEnergy = 0;
        var totalEnergy = 0;
        for (var i = 0; i < spectrum.length; i++) {
            var e = spectrum[i].level * spectrum[i].level;  // power
            totalEnergy += e;
            if (spectrum[i].freq >= 85 && spectrum[i].freq <= 4000) {
                vocalEnergy += e;
            }
        }
        // Voice is active if >60% of energy is in vocal range AND total energy is above threshold
        _voiceActive = (totalEnergy > 0.05) && (vocalEnergy / totalEnergy > 0.6);
    }

    // ─── Interaction state ───
    property int activeBand: -1
    property bool _dragging: false
    property real _lastMouseY: 0

    // ─── Range: EQ curve uses -12..+12 dB, X axis 60Hz..12kHz ───
    readonly property real minDb: -12
    readonly property real maxDb: 12
    readonly property real freqMin: 60
    readonly property real freqMax: 12000
    // ─── Log warp: <1 expands lows/mids, compresses highs ───
    // 0.7 puts F0 (85-255Hz) roughly center-left, intelligibility (4kHz) at ~85%
    readonly property real freqWarp: 0.7

    // ─── Spectrum display range: -60dB..+6dB (independent of EQ range) ───
    readonly property real specMinDb: -60
    readonly property real specMaxDb: 6
    readonly property real specRangeDb: specMaxDb - specMinDb  // 66

    // ─── Vocal frequency zones (from producerhive vocal EQ chart) ───
    // Each: {low, high, label, color}
    readonly property var vocalZones: [
        { low: 20,   high: 80,   label: "SUB",    desc: "roll off" },
        { low: 100,  high: 300,  label: "F0",     desc: "fundamental" },
        { low: 350,  high: 600,  label: "BODY",   desc: "warmth" },
        { low: 1000, high: 4000, label: "BITE",   desc: "presence" },
        { low: 5000, high: 8000, label: "BRIL",   desc: "sibilance" },
        { low: 8000, high: 12000, label: "AIR",   desc: "breathiness" }
    ]

    // ─── Vocal range markers ───
    readonly property real vocalFundamentalLow: 85    // male low
    readonly property real vocalFundamentalHigh: 255  // female high
    readonly property real vocalIntelligibilityHigh: 4000  // speech intelligibility
    readonly property real vocalHarmonicsHigh: 8000   // harmonics ceiling
    // ─── Coordinate transforms (warped log scale) ───
    function freqToX(f) {
        var logMin = Math.log(freqMin);
        var logMax = Math.log(freqMax);
        var linear = (Math.log(f) - logMin) / (logMax - logMin);
        linear = Math.max(0, Math.min(1, linear));
        return Math.pow(linear, freqWarp) * width;
    }

    function xToFreq(x) {
        var logMin = Math.log(freqMin);
        var logMax = Math.log(freqMax);
        var linear = Math.pow(x / width, 1.0 / freqWarp);
        return Math.exp(logMin + linear * (logMax - logMin));
    }

    function dbToY(db) {
        return height / 2 - (db / maxDb) * (height / 2 - 10);
    }

    function yToDb(y) {
        return ((height / 2 - y) / (height / 2 - 10)) * maxDb;
    }

    // Spectrum level (0.0..1.0) to Y coordinate
    function specToY(level) {
        var topPad = 10;
        var botPad = 24;
        var usable = height - topPad - botPad;
        return botPad + (1.0 - level) * usable;
    }

    // ─── Bell filter response ───
    function bellResponse(f, center, gain, q) {
        if (gain === 0) return 0;
        var logRatio = Math.log(f / center) / Math.LN2;
        var width = q / 2.0;
        return gain * Math.exp(-(logRatio * logRatio) / (width * width));
    }

    function eqResponse(f) {
        var total = 0;
        for (var i = 0; i < bands.length; i++) {
            total += bellResponse(f, bands[i].freq, bands[i].gain, bands[i].q);
        }
        return total;
    }

    function fmtFreq(f) {
        if (f >= 1000) return (f / 1000) + "k";
        return Math.round(f).toString();
    }

    // ─── Mouse interaction ───
    MouseArea {
        anchors.fill: parent
        cursorShape: eq._dragging ? Qt.SizeVerCursor : Qt.PointingHandCursor

        onPressed: {
            var bestIdx = -1;
            var bestDist = 30;
            for (var i = 0; i < eq.bands.length; i++) {
                var px = eq.freqToX(eq.bands[i].freq);
                var py = eq.dbToY(eq.bands[i].gain);
                var dist = Math.sqrt((mouseX - px) * (mouseX - px) + (mouseY - py) * (mouseY - py));
                if (dist < bestDist) { bestDist = dist; bestIdx = i; }
            }
            if (bestIdx >= 0) {
                eq.activeBand = bestIdx;
                eq._dragging = true;
                eq._lastMouseY = mouseY;
                var newFreq = eq.xToFreq(mouseX);
                var minF = eq.freqMin, maxF = eq.freqMax;
                if (bestIdx > 0) minF = eq.bands[bestIdx - 1].freq * 1.2;
                if (bestIdx < eq.bands.length - 1) maxF = eq.bands[bestIdx + 1].freq * 0.83;
                newFreq = Math.max(minF, Math.min(maxF, Math.round(newFreq)));
                var newGain = Math.max(eq.minDb, Math.min(eq.maxDb, Math.round(eq.yToDb(mouseY) * 10) / 10));
                eq.bands[bestIdx].freq = newFreq;
                eq.bands[bestIdx].gain = newGain;
                eq.requestPaint();
            }
        }

        onPositionChanged: {
            if (eq._dragging && eq.activeBand >= 0) {
                var newFreq = eq.xToFreq(mouseX);
                var minF = eq.freqMin, maxF = eq.freqMax;
                if (eq.activeBand > 0) minF = eq.bands[eq.activeBand - 1].freq * 1.2;
                if (eq.activeBand < eq.bands.length - 1) maxF = eq.bands[eq.activeBand + 1].freq * 0.83;
                newFreq = Math.max(minF, Math.min(maxF, Math.round(newFreq)));
                var newGain = Math.max(eq.minDb, Math.min(eq.maxDb, Math.round(eq.yToDb(mouseY) * 10) / 10));
                eq.bands[eq.activeBand].freq = newFreq;
                eq.bands[eq.activeBand].gain = newGain;
                eq.bandChanged(eq.activeBand, newFreq, newGain, eq.bands[eq.activeBand].q);
                eq.requestPaint();
            }
        }

        onReleased: { eq._dragging = false; }

        onWheel: {
            var bestIdx = -1, bestDist = 50;
            for (var i = 0; i < eq.bands.length; i++) {
                var px = eq.freqToX(eq.bands[i].freq);
                if (Math.abs(mouseX - px) < bestDist) { bestDist = Math.abs(mouseX - px); bestIdx = i; }
            }
            if (bestIdx >= 0) {
                var delta = wheel.angleDelta.y > 0 ? 0.2 : -0.2;
                eq.bands[bestIdx].q = Math.max(0.3, Math.min(6.0, Math.round((eq.bands[bestIdx].q + delta) * 10) / 10));
                eq.requestPaint();
            }
        }
    }

    onBandsChanged: requestPaint()
    onWidthChanged: requestPaint()
    onHeightChanged: requestPaint()
    onShowSpectrumChanged: requestPaint()

    Timer {
        interval: 16
        running: _smoothedSpectrum.length > 0
        repeat: true
        onTriggered: { _smoothSpectrum(); requestPaint(); }
    }

    onPaint: {
        var ctx = getContext("2d");
        ctx.reset();
        var w = width, h = height, midY = h / 2;

        // ─── Background ───
        ctx.fillStyle = bg;
        ctx.fillRect(0, 0, w, h);

        // ─── Vocal EQ zone labels along the top ───
        ctx.font = "7px monospace";
        ctx.textAlign = "center";
        ctx.textBaseline = "top";
        for (var zi = 0; zi < vocalZones.length; zi++) {
            var z = vocalZones[zi];
            var zx1 = freqToX(z.low);
            var zx2 = freqToX(z.high);
            // Subtle alternating band
            if (zi % 2 === 0) {
                ctx.fillStyle = vocalRangeColor;
                ctx.globalAlpha = 0.025;
                ctx.fillRect(zx1, 0, zx2 - zx1, h);
                ctx.globalAlpha = 1.0;
            }
            // Label at top
            ctx.fillStyle = vocalRangeColor;
            ctx.globalAlpha = 0.3;
            ctx.fillText(z.label, (zx1 + zx2) / 2, clipY + 2);
            ctx.globalAlpha = 1.0;
        }

        // ─── Clipping zone (red rectangle at +6dB and above) ───
        var clipY = specToY(1.0);
        ctx.fillStyle = clipColor;
        ctx.globalAlpha = 0.08;
        ctx.fillRect(0, 0, w, clipY);
        ctx.globalAlpha = 1.0;
        ctx.strokeStyle = clipColor;
        ctx.lineWidth = 1;
        ctx.setLineDash([4, 4]);
        ctx.globalAlpha = 0.4;
        ctx.beginPath();
        ctx.moveTo(0, clipY); ctx.lineTo(w, clipY);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.globalAlpha = 1.0;
        ctx.fillStyle = clipColor;
        ctx.globalAlpha = 0.5;
        ctx.font = "8px monospace";
        ctx.textAlign = "right";
        ctx.textBaseline = "top";
        ctx.fillText("CLIP", w - 4, 2);
        ctx.globalAlpha = 1.0;

        // ─── Grid ───
        var freqMarks = [60, 100, 200, 500, 1000, 2000, 4000, 8000, 12000];
        ctx.font = "9px monospace";
        ctx.textAlign = "center";
        ctx.textBaseline = "top";
        for (var i = 0; i < freqMarks.length; i++) {
            var gx = freqToX(freqMarks[i]);
            ctx.strokeStyle = gridMajor;
            ctx.lineWidth = 1;
            ctx.beginPath();
            ctx.moveTo(gx, 0); ctx.lineTo(gx, h);
            ctx.stroke();
            ctx.fillStyle = textColor;
            ctx.fillText(fmtFreq(freqMarks[i]), gx, h - 14);
        }

        var dbMarks = [-12, -6, 0, 6, 12];
        ctx.textAlign = "left";
        ctx.textBaseline = "middle";
        for (var i = 0; i < dbMarks.length; i++) {
            var gy = dbToY(dbMarks[i]);
            ctx.strokeStyle = dbMarks[i] === 0 ? gridMajor : gridColor;
            ctx.lineWidth = dbMarks[i] === 0 ? 1.5 : 1;
            ctx.beginPath();
            ctx.moveTo(0, gy); ctx.lineTo(w, gy);
            ctx.stroke();
            ctx.fillStyle = textColor;
            ctx.fillText((dbMarks[i] > 0 ? "+" : "") + dbMarks[i] + "dB", 3, gy - 5);
        }

        // ─── Mic spectrum (smoothed) ───
        if (showSpectrum && _smoothedSpectrum.length > 1) {
            // Use brighter color when voice is detected
            var sc = _voiceActive ? spectrumVoiceColor : spectrumColor;
            var sf = _voiceActive ? spectrumVoiceFill : spectrumFill;

            // Fill
            ctx.fillStyle = sf;
            ctx.globalAlpha = _voiceActive ? 0.3 : 0.15;
            ctx.beginPath();
            ctx.moveTo(0, h);
            for (var i = 0; i < _smoothedSpectrum.length; i++) {
                var sx = freqToX(_smoothedSpectrum[i].freq);
                var sy = specToY(_smoothedSpectrum[i].level);
                if (i === 0) ctx.lineTo(sx, sy);
                else if (i < _smoothedSpectrum.length - 1) {
                    var nx = freqToX(_smoothedSpectrum[i + 1].freq);
                    var ny = specToY(_smoothedSpectrum[i + 1].level);
                    ctx.quadraticCurveTo(sx, sy, (sx + nx) / 2, (sy + ny) / 2);
                } else {
                    ctx.lineTo(sx, sy);
                }
            }
            ctx.lineTo(w, h);
            ctx.closePath();
            ctx.fill();
            ctx.globalAlpha = 1.0;

            // Line
            ctx.strokeStyle = sc;
            ctx.lineWidth = 1.5;
            ctx.globalAlpha = _voiceActive ? 0.8 : 0.4;
            ctx.lineCap = "round";
            ctx.lineJoin = "round";
            ctx.beginPath();
            for (var i = 0; i < _smoothedSpectrum.length; i++) {
                var sx = freqToX(_smoothedSpectrum[i].freq);
                var sy = specToY(_smoothedSpectrum[i].level);
                if (i === 0) ctx.moveTo(sx, sy);
                else if (i < _smoothedSpectrum.length - 1) {
                    var nx = freqToX(_smoothedSpectrum[i + 1].freq);
                    var ny = specToY(_smoothedSpectrum[i + 1].level);
                    ctx.quadraticCurveTo(sx, sy, (sx + nx) / 2, (sy + ny) / 2);
                } else {
                    ctx.lineTo(sx, sy);
                }
            }
            ctx.stroke();
            ctx.globalAlpha = 1.0;
        }

        // ─── EQ response curve (smooth) ───
        var curvePoints = [];
        var numPoints = 256;
        for (var i = 0; i < numPoints; i++) {
            var t = i / (numPoints - 1);
            var f = freqMin * Math.pow(freqMax / freqMin, t);
            curvePoints.push({ x: freqToX(f), y: dbToY(eqResponse(f)) });
        }

        ctx.fillStyle = curveFill;
        ctx.globalAlpha = 0.12;
        ctx.beginPath();
        ctx.moveTo(0, midY);
        ctx.lineTo(curvePoints[0].x, curvePoints[0].y);
        for (var i = 1; i < curvePoints.length - 1; i++) {
            ctx.quadraticCurveTo(curvePoints[i].x, curvePoints[i].y,
                (curvePoints[i].x + curvePoints[i + 1].x) / 2,
                (curvePoints[i].y + curvePoints[i + 1].y) / 2);
        }
        ctx.lineTo(curvePoints[curvePoints.length - 1].x, curvePoints[curvePoints.length - 1].y);
        ctx.lineTo(w, midY);
        ctx.closePath();
        ctx.fill();
        ctx.globalAlpha = 1.0;

        ctx.strokeStyle = curveColor;
        ctx.lineWidth = 2.5;
        ctx.lineCap = "round";
        ctx.lineJoin = "round";
        ctx.beginPath();
        ctx.moveTo(curvePoints[0].x, curvePoints[0].y);
        for (var i = 1; i < curvePoints.length - 1; i++) {
            ctx.quadraticCurveTo(curvePoints[i].x, curvePoints[i].y,
                (curvePoints[i].x + curvePoints[i + 1].x) / 2,
                (curvePoints[i].y + curvePoints[i + 1].y) / 2);
        }
        ctx.lineTo(curvePoints[curvePoints.length - 1].x, curvePoints[curvePoints.length - 1].y);
        ctx.stroke();

        // ─── Band points ───
        for (var i = 0; i < bands.length; i++) {
            var px = freqToX(bands[i].freq);
            var py = dbToY(bands[i].gain);
            var isActive = (i === activeBand);
            var radius = isActive ? 8 : 6;

            if (isActive) {
                ctx.fillStyle = pointColor;
                ctx.globalAlpha = 0.3;
                ctx.beginPath();
                ctx.arc(px, py, radius + 6, 0, 2 * Math.PI);
                ctx.fill();
                ctx.globalAlpha = 1.0;
            }

            ctx.fillStyle = isActive ? pointActive : pointColor;
            ctx.strokeStyle = pointBorder;
            ctx.lineWidth = 2;
            ctx.beginPath();
            ctx.arc(px, py, radius, 0, 2 * Math.PI);
            ctx.fill();
            ctx.stroke();

            ctx.fillStyle = isActive ? pointActive : textColor;
            ctx.font = "9px monospace";
            ctx.textAlign = "center";
            ctx.textBaseline = "bottom";
            ctx.fillText((bands[i].gain > 0 ? "+" : "") + bands[i].gain.toFixed(1) + "dB", px, py - radius - 3);

            ctx.textBaseline = "top";
            ctx.fillStyle = textColor;
            ctx.fillText(fmtFreq(bands[i].freq), px, h - 24);

            if (isActive) {
                ctx.fillStyle = pointActive;
                ctx.font = "8px monospace";
                ctx.textAlign = "right";
                ctx.fillText("Q" + bands[i].q.toFixed(1), px + radius + 4, py - 3);
            }
        }
    }
}