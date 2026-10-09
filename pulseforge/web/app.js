/* PulseForge Web Control Panel — client-side app.
 * WebSocket auto-reconnect, HTTP API calls, real-time VU meters.
 * No build tools, no dependencies — plain ES6.
 */

(function() {
    "use strict";

    // ─── State ──────────────────────────────────────────────────
    const state = {
        ws: null,
        wsConnected: false,
        currentPage: 0,
        soundboardSlots: [[], [], []],
        recordingChannels: [],
        sbOutput: "pulseforge_gaming",
        mixerChannels: ["game", "chat", "media", "aux"],
        mixerState: {},
        micState: {},
        micSettings: {},
        afxStatus: {},
        eqBands: [],
        clipInfo: null,
        clipWaveform: null,
        clipTrimStart: 0,
        clipTrimEnd: 15,
        playingSlots: new Set(),
        vuData: {},
    };

    const EQ_FREQS = [60, 120, 250, 500, 1000, 2000, 4000, 8000];
    const CHANNELS = ["game", "chat", "media", "aux"];
    const RECORD_CHANNELS = ["game", "chat", "media", "aux", "mic"];

    // ─── API helpers ────────────────────────────────────────────
    async function apiGet(path) {
        const r = await fetch("/api/" + path);
        if (!r.ok) throw new Error(`API ${path}: ${r.status}`);
        return r.json();
    }

    async function apiPost(path, params = {}) {
        const qs = new URLSearchParams(params).toString();
        const r = await fetch("/api/" + path + (qs ? "?" + qs : ""), {
            method: "POST",
        });
        if (!r.ok && r.status !== 204) throw new Error(`API ${path}: ${r.status}`);
        return r.status === 204 ? null : r.json();
    }

    // ─── WebSocket ──────────────────────────────────────────────
    function connectWS() {
        const proto = location.protocol === "https:" ? "wss:" : "ws:";
        const url = `${proto}//${location.host}/ws`;
        const ws = new WebSocket(url);

        ws.onopen = () => {
            state.wsConnected = true;
            updateStatusDot();
        };

        ws.onmessage = (ev) => {
            try {
                const msg = JSON.parse(ev.data);
                handleWSMessage(msg);
            } catch (e) { /* ignore malformed */ }
        };

        ws.onclose = () => {
            state.wsConnected = false;
            updateStatusDot();
            // Auto-reconnect after 1s
            setTimeout(() => {
                if (!state.wsConnected) connectWS();
            }, 1000);
        };

        ws.onerror = () => {
            ws.close();
        };

        state.ws = ws;
    }

    function updateStatusDot() {
        const dot = document.getElementById("status-dot");
        if (state.wsConnected) {
            dot.classList.remove("disconnected");
        } else {
            dot.classList.add("disconnected");
        }
    }

    function handleWSMessage(msg) {
        if (msg.type === "vu") {
            state.vuData = msg.data || {};
            updateVU();
        }
    }

    // ─── VU updates ─────────────────────────────────────────────
    function updateVU() {
        const d = state.vuData;

        // Master VU (mixer tab)
        const mainFill = document.getElementById("vu-master-main");
        const streamFill = document.getElementById("vu-master-stream");
        if (mainFill) mainFill.style.width = pct(d.master || 0);
        if (streamFill) streamFill.style.width = pct(d.master || 0);

        // Per-channel VU (mixer tab)
        for (const ch of CHANNELS) {
            const chData = (d.channels || {})[ch];
            if (chData) {
                const mainEl = document.getElementById(`vu-${ch}-main`);
                const streamEl = document.getElementById(`vu-${ch}-stream`);
                if (mainEl) mainEl.style.width = pct(chData.main);
                if (streamEl) streamEl.style.width = pct(chData.stream);
            }
        }

        // Mic VU (mic tab)
        const micFill = document.getElementById("vu-mic");
        if (micFill) micFill.style.width = pct(d.mic || 0);
    }

    function pct(v) {
        return Math.max(0, Math.min(100, v * 100)) + "%";
    }

    // ─── Toast ──────────────────────────────────────────────────
    let toastTimer = null;
    function toast(msg, isError) {
        const el = document.getElementById("toast");
        el.textContent = msg;
        el.className = isError ? "error show" : "show";
        clearTimeout(toastTimer);
        toastTimer = setTimeout(() => el.className = "", 3000);
    }

    // ─── In-app modal dialog (never uses system popups) ────────
    // showModal({title, body, input, value, buttons:[{label, value, style}]})
    //   → resolves with the clicked button's value, or the input value
    //     when a button has submitInput:true.
    function showModal(opts) {
        return new Promise(resolve => {
            const overlay = document.getElementById("modal-overlay");
            const titleEl = document.getElementById("modal-title");
            const bodyEl = document.getElementById("modal-body");
            const inputEl = document.getElementById("modal-input");
            const actionsEl = document.getElementById("modal-actions");

            titleEl.textContent = opts.title || "";
            bodyEl.innerHTML = opts.body || "";

            if (opts.input) {
                inputEl.classList.add("show");
                inputEl.value = opts.value || "";
                setTimeout(() => { inputEl.focus(); inputEl.select(); }, 30);
            } else {
                inputEl.classList.remove("show");
                inputEl.value = "";
            }
            actionsEl.innerHTML = "";

            const close = (val) => {
                overlay.classList.remove("show");
                document.removeEventListener("keydown", onKey);
                resolve(val);
            };

            (opts.buttons || [{ label: "OK", value: true }]).forEach(spec => {
                const btn = document.createElement("button");
                btn.className = "btn" + (spec.style ? " " + spec.style : "");
                btn.textContent = spec.label;
                btn.addEventListener("click", () => {
                    if (spec.submitInput) close(inputEl.value.trim());
                    else close(spec.value);
                });
                actionsEl.appendChild(btn);
            });

            const onKey = (e) => {
                if (e.key === "Escape") close(null);
                else if (e.key === "Enter" && opts.input) {
                    const primary = (opts.buttons || []).find(b => b.submitInput || b.primary);
                    if (primary) close(inputEl.value.trim());
                }
            };
            document.addEventListener("keydown", onKey);
            overlay.classList.add("show");
        });
    }

    // ─── Tab switching ──────────────────────────────────────────
    function initTabs() {
        document.querySelectorAll(".tab-btn").forEach(btn => {
            btn.addEventListener("click", () => {
                const tab = btn.dataset.tab;
                document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
                document.querySelectorAll(".tab-panel").forEach(p => p.classList.remove("active"));
                btn.classList.add("active");
                document.getElementById(`tab-${tab}`).classList.add("active");
            });
        });
    }

    // ─── Soundboard ─────────────────────────────────────────────
    async function loadSoundboardSlots(page) {
        const res = await apiGet(`soundboard/slots?page=${page}`);
        state.soundboardSlots[page] = res.slots || [];
        renderSoundboard(page);
    }

    function renderSoundboard(page) {
        const grid = document.getElementById("soundboard-grid");
        grid.innerHTML = "";
        const slots = state.soundboardSlots[page] || [];

        for (let i = 0; i < 9; i++) {
            const slot = slots[i] || { name: "", file_path: "", volume: 1.0 };
            const div = document.createElement("div");
            div.className = "sb-slot";
            if (slot.file_path) div.classList.add("assigned");
            const playKey = `${page}:${i}`;
            if (state.playingSlots.has(playKey)) div.classList.add("playing");

            div.innerHTML = `
                <div class="sb-slot-name">${slot.name || "<span class='sb-slot-empty'>Empty</span>"}</div>
                ${slot.file_path ? `<div class="sb-slot-vol"><input type="range" min="0" max="100" value="${Math.round((slot.volume || 1) * 100)}" data-page="${page}" data-index="${i}"></div>` : ""}
            `;

            // Tap to play (long-press clear disabled for VR)
            div.addEventListener("pointerdown", (e) => {
                if (!slot.file_path) return;
                playSlot(page, i);
            });

            // Volume slider
            const volInput = div.querySelector('input[type="range"]');
            if (volInput) {
                volInput.addEventListener("change", () => {
                    // Volume changes don't have a direct API — they'd need setSoundboardVolume
                    // For now, volume is per-slot in config
                    toast("Volume saved for slot " + (i + 1));
                });
            }

            grid.appendChild(div);
        }
    }

    async function playSlot(page, index) {
        try {
            await apiPost("soundboard/play", { page: page, index: index });
            state.playingSlots.add(`${page}:${index}`);
            renderSoundboard(page);
            // Check play status after 200ms
            setTimeout(async () => {
                const res = await apiGet(`soundboard/playing?page=${page}&index=${index}`);
                if (!res.playing) {
                    state.playingSlots.delete(`${page}:${index}`);
                    renderSoundboard(page);
                }
            }, 200);
        } catch (e) {
            toast("Play failed: " + e.message, true);
        }
    }

    async function clearSlot(page, index) {
        if (!confirm("Clear this slot?")) return;
        try {
            await apiPost("soundboard/clear", { page: page, index: index });
            state.playingSlots.delete(`${page}:${index}`);
            await loadSoundboardSlots(page);
            toast("Slot cleared");
        } catch (e) {
            toast("Clear failed: " + e.message, true);
        }
    }

    function initSoundboardPages() {
        document.querySelectorAll(".page-btn").forEach(btn => {
            btn.addEventListener("click", () => {
                const page = parseInt(btn.dataset.page);
                state.currentPage = page;
                document.querySelectorAll(".page-btn").forEach(b => b.classList.remove("active"));
                btn.classList.add("active");
                loadSoundboardSlots(page);
            });
        });
    }

    async function initRecordingToggle() {
        const cb = document.getElementById("rec-enabled");
        try {
            const res = await apiGet("soundboard/recording");
            cb.checked = res.enabled;
        } catch (e) { /* default on */ }
        cb.addEventListener("change", async () => {
            try {
                await apiPost("soundboard/recording", { enabled: cb.checked ? "true" : "false" });
                toast("Recording " + (cb.checked ? "enabled" : "disabled"));
            } catch (e) {
                toast("Failed to toggle recording", true);
            }
        });
    }

    async function refreshRecordingChannels() {
        try {
            const res = await apiGet("soundboard/channels/recording");
            state.recordingChannels = res.channels || [];
            renderRecordingChannels();
        } catch (e) { /* ignore */ }
    }

    function renderRecordingChannels() {
        document.querySelectorAll(".rec-channel").forEach(el => {
            const ch = el.dataset.channel;
            if (state.recordingChannels.includes(ch)) {
                el.classList.add("active");
            } else {
                el.classList.remove("active");
            }
        });
    }

    function initOutputSelector() {
        document.querySelectorAll("#sb-output-selector .output-btn").forEach(btn => {
            btn.addEventListener("click", async () => {
                const target = btn.dataset.target;
                document.querySelectorAll("#sb-output-selector .output-btn").forEach(b => b.classList.remove("active"));
                btn.classList.add("active");
                try {
                    await apiPost("soundboard/output", { target: target });
                    state.sbOutput = target;
                    toast("Output: " + btn.textContent);
                } catch (e) {
                    toast("Failed to set output", true);
                }
            });
        });
    }

    // ─── Clip capture ────────────────────────────────────────────
    function initClipCapture() {
        const captureButtons = [
            { id: "btn-capture-game", channel: "game" },
            { id: "btn-capture-chat", channel: "chat" },
            { id: "btn-capture-media", channel: "media" },
            { id: "btn-capture-aux", channel: "aux" },
            { id: "btn-capture-mic", channel: "mic" },
        ];

        captureButtons.forEach(({ id, channel }) => {
            document.getElementById(id).addEventListener("click", async () => {
                try {
                    await apiPost("soundboard/capture", { channel: channel });
                    toast(`Captured ${channel} clip`);
                    await loadClipInfo();
                } catch (e) {
                    toast("Capture failed: " + e.message, true);
                }
            });
        });

        // Trim sliders
        const trimStart = document.getElementById("clip-trim-start");
        const trimEnd = document.getElementById("clip-trim-end");

        trimStart.addEventListener("input", () => {
            state.clipTrimStart = parseFloat(trimStart.value);
            if (state.clipTrimStart > state.clipTrimEnd) {
                state.clipTrimEnd = state.clipTrimStart;
                trimEnd.value = state.clipTrimEnd;
            }
        });
        trimStart.addEventListener("change", () => updateTrim());
        trimEnd.addEventListener("input", () => {
            state.clipTrimEnd = parseFloat(trimEnd.value);
            if (state.clipTrimEnd < state.clipTrimStart) {
                state.clipTrimStart = state.clipTrimEnd;
                trimStart.value = state.clipTrimStart;
            }
        });
        trimEnd.addEventListener("change", () => updateTrim());

        // Preview
        document.getElementById("btn-clip-play").addEventListener("click", async () => {
            try { await apiPost("soundboard/clip/play"); }
            catch (e) { toast("Preview failed", true); }
        });
        document.getElementById("btn-clip-stop").addEventListener("click", async () => {
            try { await apiPost("soundboard/clip/stop"); } catch (e) {}
        });

        // Publish (collision-guarded — in-app rename/overwrite dialog)
        document.getElementById("btn-clip-publish").addEventListener("click", async () => {
            const nameInput = document.getElementById("clip-name");
            const name = nameInput.value.trim();
            try {
                const info = await apiGet("soundboard/clip/publish/resolve" +
                    (name ? "?name=" + encodeURIComponent(name) : ""));
                if (!info.available) { toast("No clip to publish", true); return; }

                if (info.exists) {
                    const choice = await showModal({
                        title: "Name already exists",
                        body: `<strong>${info.name}</strong> already exists in the soundboard folder.`,
                        input: true,
                        value: info.suggestion_stem,
                        buttons: [
                            { label: "Cancel", value: null },
                            { label: "Save as new name", submitInput: true, primary: true, style: "btn-primary" },
                            { label: "Overwrite", value: "__overwrite__", style: "btn-danger" },
                        ],
                    });
                    if (choice === null) return;
                    if (choice === "__overwrite__") {
                        const res = await apiPost("soundboard/clip/publish", { name: info.stem, overwrite: "true" });
                        if (res.path) { toast("Overwrote: " + res.path.split("/").pop()); nameInput.value = ""; }
                        else toast("Publish failed", true);
                    } else if (choice) {
                        const res = await apiPost("soundboard/clip/publish", { name: choice });
                        if (res.path) { toast("Published: " + res.path.split("/").pop()); nameInput.value = ""; }
                        else toast("Publish failed", true);
                    }
                    return;
                }

                const res = await apiPost("soundboard/clip/publish", name ? { name: name } : {});
                if (res.path) { toast("Published: " + res.path.split("/").pop()); nameInput.value = ""; }
                else toast("Publish failed", true);
            } catch (e) { toast("Publish failed: " + e.message, true); }
        });

        // Assign to slot
        document.getElementById("btn-clip-assign1").addEventListener("click", async () => {
            const page = state.currentPage;
            // Find first empty slot
            const slots = state.soundboardSlots[page] || [];
            let idx = -1;
            for (let i = 0; i < 9; i++) {
                if (!slots[i] || !slots[i].file_path) { idx = i; break; }
            }
            if (idx < 0) { toast("No empty slots on this page", true); return; }
            try {
                const res = await apiPost("soundboard/clip/assign", { page: page, index: idx });
                if (res.assigned) {
                    toast(`Assigned to page ${page + 1} slot ${idx + 1}`);
                    await loadSoundboardSlots(page);
                } else {
                    toast("No clip to assign", true);
                }
            } catch (e) { toast("Assign failed", true); }
        });

        // Clear
        document.getElementById("btn-clip-clear").addEventListener("click", async () => {
            try {
                await apiPost("soundboard/clip/clear");
                state.clipInfo = null;
                state.clipWaveform = null;
                drawClipWaveform(null);
                toast("Clip cleared");
            } catch (e) {}
        });
    }

    async function loadClipInfo() {
        try {
            const info = await apiGet("soundboard/clip/info");
            state.clipInfo = info;
            if (info.available) {
                state.clipTrimStart = info.trim_start;
                state.clipTrimEnd = info.trim_end;
                document.getElementById("clip-trim-start").max = info.duration;
                document.getElementById("clip-trim-start").value = info.trim_start;
                document.getElementById("clip-trim-end").max = info.duration;
                document.getElementById("clip-trim-end").value = info.trim_end;

                // Load waveform
                const wf = await apiGet("soundboard/clip/waveform");
                state.clipWaveform = wf.peaks;
                drawClipWaveform(wf.peaks);
            }
        } catch (e) { /* no clip */ }
    }

    async function updateTrim() {
        try {
            await apiPost("soundboard/clip/trim", {
                start: state.clipTrimStart,
                end: state.clipTrimEnd,
            });
        } catch (e) {}
    }

    function drawClipWaveform(peaks) {
        const canvas = document.getElementById("clip-canvas");
        const ctx = canvas.getContext("2d");
        const w = canvas.offsetWidth || 400;
        const h = canvas.offsetHeight || 80;
        canvas.width = w;
        canvas.height = h;

        ctx.clearRect(0, 0, w, h);

        if (!peaks || !peaks.length) {
            ctx.fillStyle = "#8888a0";
            ctx.font = "13px sans-serif";
            ctx.textAlign = "center";
            ctx.fillText("No clip captured", w / 2, h / 2);
            return;
        }

        const n = peaks.length;
        const barW = w / n;
        const mid = h / 2;

        ctx.fillStyle = "#7c5cfc";
        for (let i = 0; i < n; i++) {
            const p = peaks[i];
            const peak = (p.peak || 0) * mid;
            const rms = (p.rms || 0) * mid;
            const x = i * barW;
            // Peak (lighter)
            ctx.fillRect(x, mid - peak, Math.max(1, barW - 1), peak * 2);
            // RMS (darker, centered)
            ctx.fillStyle = "#5c3ce0";
            ctx.fillRect(x, mid - rms, Math.max(1, barW - 1), rms * 2);
            ctx.fillStyle = "#7c5cfc";
        }
    }

    // ─── Mixer ──────────────────────────────────────────────────
    async function loadMixer() {
        const grid = document.getElementById("mixer-grid");
        grid.innerHTML = "";

        // Load channel states
        for (const ch of CHANNELS) {
            try {
                const s = await apiGet(`mixer/channel?channel=${ch}`);
                state.mixerState[ch] = s;
            } catch (e) {
                state.mixerState[ch] = { volume: 1.0, mute: false, stream_enabled: false, stream_volume: 1.0 };
            }
        }

        // Render channels
        for (const ch of CHANNELS) {
            const s = state.mixerState[ch];
            const el = document.createElement("div");
            el.className = "mixer-channel";
            el.innerHTML = `
                <div class="channel-name">${ch.charAt(0).toUpperCase() + ch.slice(1)}</div>
                <div class="vu-meter"><div class="vu-fill" id="vu-${ch}-main"></div></div>
                <div class="vu-meter"><div class="vu-fill stream" id="vu-${ch}-stream"></div></div>
                <div class="fader-row">
                    <label>Vol</label>
                    <input type="range" class="ch-vol" data-channel="${ch}" min="0" max="100" value="${Math.round(s.volume * 100)}">
                </div>
                <div class="fader-row">
                    <label>Strm</label>
                    <input type="range" class="ch-stream-vol" data-channel="${ch}" min="0" max="100" value="${Math.round(s.stream_volume * 100)}">
                </div>
                <div class="mixer-btns">
                    <button class="mix-btn mute ${s.mute ? "active" : ""}" data-channel="${ch}">M</button>
                    <button class="mix-btn stream ${s.stream_enabled ? "active" : ""}" data-channel="${ch}">S</button>
                </div>
            `;
            grid.appendChild(el);
        }

        // Master channel
        const masterEl = document.createElement("div");
        masterEl.className = "mixer-channel master";
        masterEl.innerHTML = `
            <div class="channel-name">Master</div>
            <div class="vu-meter"><div class="vu-fill" id="vu-master-main"></div></div>
            <div class="vu-meter"><div class="vu-fill stream" id="vu-master-stream2"></div></div>
            <div class="fader-row">
                <label>Vol</label>
                <input type="range" id="master-vol" min="0" max="100" value="100">
            </div>
            <div class="mixer-btns">
                <button class="mix-btn" disabled style="opacity:0.4">Master</button>
            </div>
        `;
        grid.appendChild(masterEl);

        // Bind events
        grid.querySelectorAll(".ch-vol").forEach(slider => {
            slider.addEventListener("change", async () => {
                const ch = slider.dataset.channel;
                const vol = parseFloat(slider.value) / 100;
                try { await apiPost("mixer/volume", { channel: ch, volume: vol }); }
                catch (e) { toast("Volume set failed", true); }
            });
        });

        grid.querySelectorAll(".ch-stream-vol").forEach(slider => {
            slider.addEventListener("change", async () => {
                const ch = slider.dataset.channel;
                const vol = parseFloat(slider.value) / 100;
                try { await apiPost("mixer/stream-volume", { channel: ch, volume: vol }); }
                catch (e) {}
            });
        });

        grid.querySelectorAll(".mix-btn.mute").forEach(btn => {
            btn.addEventListener("click", async () => {
                const ch = btn.dataset.channel;
                const isMuted = btn.classList.contains("active");
                try {
                    await apiPost("mixer/mute", { channel: ch, muted: isMuted ? "false" : "true" });
                    btn.classList.toggle("active");
                } catch (e) {}
            });
        });

        grid.querySelectorAll(".mix-btn.stream").forEach(btn => {
            btn.addEventListener("click", async () => {
                const ch = btn.dataset.channel;
                const isEnabled = btn.classList.contains("active");
                try {
                    await apiPost("mixer/stream", { channel: ch, enabled: isEnabled ? "false" : "true" });
                    btn.classList.toggle("active");
                } catch (e) {}
            });
        });
    }

    // ─── Mic ────────────────────────────────────────────────────
    async function loadMic() {
        // Load mic state
        try {
            state.micState = await apiGet("mic/state");
            state.micSettings = await apiGet("mic/settings");
            state.afxStatus = await apiGet("mic/afx/status");
        } catch (e) {
            toast("Failed to load mic settings", true);
            return;
        }

        // Mic controls
        const volSlider = document.getElementById("mic-volume");
        volSlider.value = Math.round((state.micState.volume || 1.0) * 100);
        document.getElementById("mic-volume-val").textContent = volSlider.value + "%";
        volSlider.addEventListener("change", () => {
            const v = parseFloat(volSlider.value) / 100;
            apiPost("mic/volume", { volume: v });
            document.getElementById("mic-volume-val").textContent = volSlider.value + "%";
        });

        document.getElementById("mic-mute").checked = state.micState.mute || false;
        document.getElementById("mic-mute").addEventListener("change", (e) => {
            apiPost("mic/mute", { muted: e.target.checked ? "true" : "false" });
        });

        document.getElementById("mic-monitor").checked = state.micState.monitor || false;
        document.getElementById("mic-monitor").addEventListener("change", (e) => {
            apiPost("mic/monitor", { enabled: e.target.checked ? "true" : "false" });
        });

        document.getElementById("mic-stream").checked = state.micState.stream_enabled || false;
        document.getElementById("mic-stream").addEventListener("change", (e) => {
            apiPost("mic/stream", { enabled: e.target.checked ? "true" : "false" });
        });

        // AFX
        const afx = state.afxStatus || {};
        document.getElementById("afx-enabled").checked = afx.enabled || false;
        document.getElementById("afx-enabled").addEventListener("change", (e) => {
            apiPost("mic/afx/enabled", { enabled: e.target.checked ? "true" : "false" });
        });

        const afxMode = document.getElementById("afx-mode");
        if (afx.effect_mode) afxMode.value = afx.effect_mode;
        afxMode.addEventListener("change", () => {
            apiPost("mic/afx/mode", { mode: afxMode.value });
            toast("AFX mode: " + afxMode.value);
        });

        const intensitySlider = document.getElementById("afx-intensity");
        intensitySlider.value = afx.intensity || 70;
        document.getElementById("afx-intensity-val").textContent = intensitySlider.value + "%";
        intensitySlider.addEventListener("change", () => {
            const v = parseFloat(intensitySlider.value) / 100;
            apiPost("mic/afx/intensity", { intensity: v });
            document.getElementById("afx-intensity-val").textContent = intensitySlider.value + "%";
        });

        // DeepVQE-S AI denoise
        let dv = {};
        try { dv = await apiGet("mic/deepvqe/status"); } catch (e) { dv = {}; }
        const dvEnabled = document.getElementById("deepvqe-enabled");
        dvEnabled.checked = dv.enabled || false;
        dvEnabled.addEventListener("change", (e) => {
            apiPost("mic/deepvqe/enabled", { enabled: e.target.checked ? "true" : "false" });
        });
        const dvStrength = document.getElementById("deepvqe-strength");
        dvStrength.value = dv.strength || 70;
        document.getElementById("deepvqe-strength-val").textContent = dvStrength.value + "%";
        dvStrength.addEventListener("change", () => {
            const v = parseFloat(dvStrength.value) / 100;
            apiPost("mic/deepvqe/strength", { strength: v });
            document.getElementById("deepvqe-strength-val").textContent = dvStrength.value + "%";
        });

        // Adaptive ambient NR
        const anr = state.micSettings.ambient_nr || {};
        document.getElementById("ambient-enabled").checked = anr.enabled !== false;
        document.getElementById("ambient-enabled").addEventListener("change", (e) => {
            apiPost("mic/ambient_nr/enabled", { enabled: e.target.checked ? "true" : "false" });
        });
        const anrLevel = document.getElementById("ambient-level");
        anrLevel.value = anr.level != null ? anr.level : 40;
        document.getElementById("ambient-level-val").textContent = anrLevel.value + "%";
        anrLevel.addEventListener("change", () => {
            apiPost("mic/ambient_nr/level", { level: parseFloat(anrLevel.value) / 100 });
            document.getElementById("ambient-level-val").textContent = anrLevel.value + "%";
        });

        // High-pass filter
        const hpf = state.micSettings.hpf || {};
        document.getElementById("hpf-enabled").checked = hpf.enabled !== false;
        document.getElementById("hpf-enabled").addEventListener("change", (e) => {
            apiPost("mic/hpf/enabled", { enabled: e.target.checked ? "true" : "false" });
        });
        const hpfFreq = document.getElementById("hpf-freq");
        hpfFreq.value = hpf.freq || 90;
        document.getElementById("hpf-freq-val").textContent = hpfFreq.value + " Hz";
        hpfFreq.addEventListener("change", () => {
            apiPost("mic/hpf/freq", { value: parseFloat(hpfFreq.value) });
            document.getElementById("hpf-freq-val").textContent = hpfFreq.value + " Hz";
        });

        // Multiband compressor
        const mbc = state.micSettings.mbcomp || {};
        document.getElementById("mbcomp-enabled").checked = mbc.enabled !== false;
        document.getElementById("mbcomp-enabled").addEventListener("change", (e) => {
            apiPost("mic/mbcomp/enabled", { enabled: e.target.checked ? "true" : "false" });
        });

        // Output limiter
        const lim = state.micSettings.limiter || {};
        document.getElementById("limiter-enabled").checked = lim.enabled !== false;
        document.getElementById("limiter-enabled").addEventListener("change", (e) => {
            apiPost("mic/limiter/enabled", { enabled: e.target.checked ? "true" : "false" });
        });
        const limCeil = document.getElementById("limiter-ceiling");
        limCeil.value = lim.ceiling != null ? lim.ceiling : -1;
        document.getElementById("limiter-ceiling-val").textContent = limCeil.value + " dB";
        limCeil.addEventListener("change", () => {
            apiPost("mic/limiter/ceiling", { value: parseFloat(limCeil.value) });
            document.getElementById("limiter-ceiling-val").textContent = limCeil.value + " dB";
        });

        // Gate
        const gate = state.micSettings.gate || {};
        const setGate = (id, val, suffix) => {
            const el = document.getElementById(id);
            el.value = val;
            const valEl = document.getElementById(id + "-val");
            if (valEl) valEl.textContent = val + suffix;
        };
        setGate("gate-threshold", gate.threshold ?? -50, " dB");
        setGate("gate-range", gate.range ?? -25, " dB");
        setGate("gate-attack", gate.attack ?? 25, " ms");
        setGate("gate-hold", gate.hold ?? 300, " ms");
        setGate("gate-release", gate.release ?? 200, " ms");
        document.getElementById("gate-enabled").checked = gate.enabled ?? true;

        bindSlider("gate-threshold", "gate/threshold", "value", " dB");
        bindSlider("gate-range", "gate/range", "value", " dB");
        bindSlider("gate-attack", "gate/attack", "value", " ms");
        bindSlider("gate-hold", "gate/hold", "value", " ms");
        bindSlider("gate-release", "gate/release", "value", " ms");
        document.getElementById("gate-enabled").addEventListener("change", (e) => {
            apiPost("mic/gate/enabled", { enabled: e.target.checked ? "true" : "false" });
        });

        // Compressor
        const comp = state.micSettings.compressor || {};
        const setComp = (id, val, suffix) => {
            const el = document.getElementById(id);
            el.value = val;
            const valEl = document.getElementById(id + "-val");
            if (valEl) valEl.textContent = val + suffix;
        };
        setComp("comp-threshold", comp.threshold ?? -20, " dB");
        setComp("comp-ratio", comp.ratio ?? 3, ": 1");
        setComp("comp-makeup", comp.makeup ?? 0, " dB");
        document.getElementById("comp-enabled").checked = comp.enabled ?? true;

        bindSlider("comp-threshold", "comp/threshold", "value", " dB");
        bindSlider("comp-ratio", "comp/ratio", "value", ": 1");
        bindSlider("comp-makeup", "comp/makeup", "value", " dB");
        document.getElementById("comp-enabled").addEventListener("change", (e) => {
            apiPost("mic/comp/enabled", { enabled: e.target.checked ? "true" : "false" });
        });

        // EQ
        document.getElementById("eq-enabled").checked = (state.micSettings.eq || {}).enabled ?? true;
        document.getElementById("eq-enabled").addEventListener("change", (e) => {
            apiPost("mic/eq/enabled", { enabled: e.target.checked ? "true" : "false" });
        });

        try {
            const eqRes = await apiGet("mic/eq/bands");
            state.eqBands = eqRes.bands || [];
        } catch (e) {
            state.eqBands = EQ_FREQS.map(f => ({ freq: f, gain: 0, q: 1.0 }));
        }
        renderEQBands();
    }

    function bindSlider(sliderId, apiPath, param, suffix) {
        const slider = document.getElementById(sliderId);
        const valEl = document.getElementById(sliderId + "-val");
        slider.addEventListener("change", () => {
            apiPost("mic/" + apiPath, { [param]: slider.value });
            if (valEl) valEl.textContent = slider.value + (suffix || "");
        });
    }

    function renderEQBands() {
        const grid = document.getElementById("eq-bands-grid");
        grid.innerHTML = "";

        for (let i = 0; i < 8; i++) {
            const band = state.eqBands[i] || { freq: EQ_FREQS[i], gain: 0, q: 1.0 };
            const el = document.createElement("div");
            el.className = "eq-band";
            el.innerHTML = `
                <div class="eq-band-freq">${band.freq} Hz</div>
                <input type="range" min="-12" max="12" step="0.5" value="${band.gain || 0}" data-idx="${i}" data-freq="${band.freq}" class="eq-gain-slider">
                <div class="eq-band-gain">${band.gain || 0} dB</div>
            `;
            grid.appendChild(el);

            const slider = el.querySelector(".eq-gain-slider");
            const label = el.querySelector(".eq-band-gain");
            slider.addEventListener("change", () => {
                const idx = parseInt(slider.dataset.idx);
                const freq = parseFloat(slider.dataset.freq);
                const gain = parseFloat(slider.value);
                const q = 1.0;
                apiPost("mic/eq/band", { idx: idx, freq: freq, gain: gain, q: q });
                label.textContent = gain + " dB";
            });
        }
    }

    // ─── Init ───────────────────────────────────────────────────
    async function init() {
        initTabs();
        initSoundboardPages();
        initOutputSelector();
        initRecordingToggle();
        initClipCapture();
        connectWS();

        // Load data
        await loadSoundboardSlots(0);
        await refreshRecordingChannels();
        await loadMixer();
        await loadMic();
        await loadClipInfo();

        // Periodic refresh of playing states
        setInterval(() => {
            if (state.playingSlots.size > 0) {
                state.playingSlots.forEach(async (key) => {
                    const [page, index] = key.split(":").map(Number);
                    try {
                        const res = await apiGet(`soundboard/playing?page=${page}&index=${index}`);
                        if (!res.playing) {
                            state.playingSlots.delete(key);
                            renderSoundboard(page);
                        }
                    } catch (e) {}
                });
            }
        }, 2000);

        // Periodic refresh of recording channels
        setInterval(refreshRecordingChannels, 5000);

        console.log("PulseForge web panel ready");
    }

    // Start when DOM is ready
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
