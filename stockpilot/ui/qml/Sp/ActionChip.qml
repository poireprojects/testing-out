import QtQuick

// BUY / SELL / HOLD / SKIP indicator with an arrow — the app's universal decision badge.
Pill {
    property string action: ""
    readonly property string a: action.toLowerCase()
    text: a === "buy" ? "BUY" : a === "sell" ? "SELL" : a === "hold" ? "HOLD" : a === "skip" ? "SKIP"
        : a === "watch" ? "WATCH" : a === "error" ? "NO ANSWER" : a.toUpperCase()
    kind: a === "buy" ? "buy" : a === "sell" ? "sell" : a === "error" ? "warn" : a === "watch" ? "info" : "hold"
    icon: a === "buy" ? "up" : a === "sell" ? "down" : a === "hold" || a === "skip" ? "pause" : a === "watch" ? "eye" : ""
}
