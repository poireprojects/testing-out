pragma Singleton
import QtQuick

QtObject {
    // surfaces — deep graphite
    readonly property color bg: "#0B0C0E"
    readonly property color sidebar: "#0E1012"
    readonly property color surface: "#141619"
    readonly property color surface2: "#1A1D21"
    readonly property color surface3: "#22262B"
    readonly property color border: "#22262B"
    readonly property color borderStrong: "#2F343B"
    // text
    readonly property color text: "#F1F3F0"
    readonly property color textDim: "#A3AAB1"
    readonly property color textMuted: "#6B727A"
    // accents — electric lime
    readonly property color lime: "#C8FF3C"
    readonly property color limeDeep: "#A8E21F"
    readonly property color limeInk: "#11170A"
    readonly property color limeSoft: Qt.rgba(0.784, 1.0, 0.235, 0.11)
    readonly property color limeGlow: Qt.rgba(0.784, 1.0, 0.235, 0.22)
    readonly property color coral: "#FF6B5B"
    readonly property color coralSoft: Qt.rgba(1.0, 0.42, 0.357, 0.13)
    readonly property color amber: "#FFC857"
    readonly property color amberSoft: Qt.rgba(1.0, 0.784, 0.341, 0.13)
    readonly property color sky: "#7CC4FF"
    readonly property color skySoft: Qt.rgba(0.486, 0.769, 1.0, 0.12)
    readonly property color slate: "#AEB6BF"
    readonly property color slateSoft: Qt.rgba(1, 1, 1, 0.06)

    readonly property string display: "Space Grotesk"
    readonly property string body: "Inter"
    readonly property int radius: 18
    readonly property int radiusSm: 11
    readonly property int gap: 16

    readonly property var icons: ({
        "overview": "M4 4h7v7H4z M13 4h7v4h-7z M13 10h7v10h-7z M4 13h7v7H4z",
        "scanner": "M12 12m-8.5 0a8.5 8.5 0 1 0 17 0a8.5 8.5 0 1 0 -17 0 M12 12m-4.5 0a4.5 4.5 0 1 0 9 0a4.5 4.5 0 1 0 -9 0 M12 12L18.2 5.8",
        "ai": "M12 3.5l1.9 5.1 5.1 1.9-5.1 1.9L12 17.5l-1.9-5.1L5 10.5l5.1-1.9z M18.5 15l.9 2.1 2.1.9-2.1.9-.9 2.1-.9-2.1-2.1-.9 2.1-.9z",
        "trades": "M5 8h14 M15 4l4 4-4 4 M19 16H5 M9 12l-4 4 4 4",
        "performance": "M4 4v16h16 M7.5 15l3.5-4 3 2.5 5-6.5",
        "settings": "M4 7h9 M17 7h3 M15 5v4 M4 12h3 M11 12h9 M9 10v4 M4 17h11 M19 17h1 M17 15v4",
        "stop": "M8.3 3h7.4L21 8.3v7.4L15.7 21H8.3L3 15.7V8.3z M9 12h6",
        "up": "M12 19V5 M6.5 10.5L12 5l5.5 5.5",
        "down": "M12 5v14 M6.5 13.5L12 19l5.5-5.5",
        "pause": "M9 6v12 M15 6v12",
        "check": "M5 12.5l4.5 4.5L19 7.5",
        "alert": "M12 4l9 16H3z M12 10v4.5 M12 17.3v.2",
        "bolt": "M13 3L5.5 13.5h6L10.5 21 18.5 10h-6z",
        "plug": "M9 3v5 M15 3v5 M6.5 8h11v3.5a5.5 5.5 0 0 1-11 0z M12 17v4",
        "data": "M4 7c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3z M4 7v10c0 1.7 3.6 3 8 3s8-1.3 8-3V7 M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3",
        "shield": "M12 3l7.5 3v5.5c0 4.7-3.2 8-7.5 9.5-4.3-1.5-7.5-4.8-7.5-9.5V6z M8.8 12.2l2.2 2.2 4.3-4.6",
        "close": "M6.5 6.5l11 11 M17.5 6.5l-11 11",
        "folder": "M3.5 6.5h6l2 2h9v10h-17z",
        "refresh": "M19.5 8A8 8 0 1 0 20 13 M20 4v4.5h-4.5",
        "clock": "M12 12m-8.5 0a8.5 8.5 0 1 0 17 0a8.5 8.5 0 1 0 -17 0 M12 7.5V12l3 2",
        "eye": "M2.5 12s3.5-6.5 9.5-6.5 9.5 6.5 9.5 6.5-3.5 6.5-9.5 6.5S2.5 12 2.5 12z M12 12m-2.8 0a2.8 2.8 0 1 0 5.6 0a2.8 2.8 0 1 0 -5.6 0",
        "flask": "M9.5 3.5h5 M10.5 3.5v5.5L5 19a1.3 1.3 0 0 0 1.1 2h11.8a1.3 1.3 0 0 0 1.1-2l-5.5-10V3.5 M7.5 15h9",
        "coin": "M12 12m-8.5 0a8.5 8.5 0 1 0 17 0a8.5 8.5 0 1 0 -17 0 M14.8 9.2c-.5-.9-1.5-1.4-2.8-1.4-1.6 0-2.8.8-2.8 2s1.1 1.7 2.8 2.1 2.8 1 2.8 2.2-1.2 2-2.8 2c-1.3 0-2.4-.6-2.9-1.5 M12 6v1.8 M12 16.2V18",
        "sparkle": "M12 4l1.6 6.4L20 12l-6.4 1.6L12 20l-1.6-6.4L4 12l6.4-1.6z",
        "moon": "M19.5 14.5A8 8 0 0 1 9.5 4.5a8 8 0 1 0 10 10z",
        "arrowRight": "M5 12h14 M13.5 6.5L19 12l-5.5 5.5",
        "lock": "M6.5 11h11v9h-11z M8.5 11V8a3.5 3.5 0 0 1 7 0v3",
        "winMin": "M6.5 12.5h11",
        "winMax": "M7 7h10v10H7z",
        "winRestore": "M9.5 7h7.5v7.5 M7 9.5h7.5V17H7z",
        "winClose": "M7 7l10 10 M17 7L7 17",
        "brain": "M9 4.5a3 3 0 0 0-3 3 3 3 0 0 0-1.5 5.5A3 3 0 0 0 7.5 18a2.5 2.5 0 0 0 4.5 1.5V6a2.5 2.5 0 0 0-3-1.5z M15 4.5a3 3 0 0 1 3 3 3 3 0 0 1 1.5 5.5A3 3 0 0 1 16.5 18a2.5 2.5 0 0 1-4.5 1.5"
    })
    readonly property int titleBarHeight: 50
    readonly property color titleBar: "#0D0F11"
    readonly property color closeHover: "#D9403A"

    // ---------------------------------------------------------- formatting
    function fixed(v, d) {
        if (v === undefined || v === null || isNaN(v)) return "—"
        return Number(v).toLocaleString(Qt.locale("en_US"), 'f', d === undefined ? 2 : d)
    }
    function money(v, d) {
        if (v === undefined || v === null || isNaN(v)) return "—"
        var s = fixed(Math.abs(v), d === undefined ? 2 : d)
        return (v <= -0.005 ? "−$" : "$") + s
    }
    function signedMoney(v, d) {
        if (v === undefined || v === null || isNaN(v)) return "—"
        var nd = d === undefined ? 2 : d
        var s = fixed(Math.abs(v), nd)
        var half = 0.5 * Math.pow(10, -nd)
        return (v >= half ? "+$" : (v <= -half ? "−$" : "$")) + s
    }
    function pct(v, d) {
        if (v === undefined || v === null || isNaN(v)) return "—"
        return fixed(v, d === undefined ? 2 : d) + "%"
    }
    function signedPct(v, d) {
        if (v === undefined || v === null || isNaN(v)) return "—"
        var nd = d === undefined ? 2 : d
        var s = fixed(Math.abs(v), nd)
        var half = 0.5 * Math.pow(10, -nd)          // values that round to zero at this precision get no sign
        return (v >= half ? "+" : (v <= -half ? "−" : "")) + s + "%"
    }
    function pnlColor(v) {
        if (v === undefined || v === null || isNaN(v)) return textDim
        return v >= 0.005 ? lime : (v <= -0.005 ? coral : textDim)
    }
    function compact(v) {
        if (v === undefined || v === null || isNaN(v)) return "—"
        var a = Math.abs(v)
        if (a >= 1e9) return fixed(v / 1e9, 1) + "B"
        if (a >= 1e6) return fixed(v / 1e6, 1) + "M"
        if (a >= 1e3) return fixed(v / 1e3, 1) + "K"
        return fixed(v, 0)
    }
    function ago(iso) {
        if (!iso) return "never"
        var t = new Date(iso)
        var s = Math.max(0, Math.round((Date.now() - t.getTime()) / 1000))
        if (s < 60) return s + "s ago"
        if (s < 3600) return Math.round(s / 60) + "m ago"
        return Math.round(s / 3600) + "h ago"
    }
    function agoSecs(s) {
        if (s === undefined || s === null || s < 0) return "not yet"
        if (s < 60) return Math.round(s) + "s ago"
        if (s < 3600) return Math.round(s / 60) + "m ago"
        return Math.round(s / 3600) + "h ago"
    }
    function healthColor(ok) {
        if (ok === true) return lime
        if (ok === false) return coral
        return amber
    }
    function modeColor(mode) {
        return mode === "live" ? coral : (mode === "paper" ? sky : amber)
    }
}
