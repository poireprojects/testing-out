import QtQuick

// Number that glides to its new value instead of jumping.
Txt {
    id: root
    property real value: 0
    property int decimals: 2
    property string format: "money"   // money | signedMoney | pct | signedPct | plain
    property real shown: value
    Behavior on shown { NumberAnimation { duration: 700; easing.type: Easing.OutCubic } }
    font.family: Theme.display
    text: format === "money" ? Theme.money(shown, decimals)
        : format === "signedMoney" ? Theme.signedMoney(shown, decimals)
        : format === "pct" ? Theme.pct(shown, decimals)
        : format === "signedPct" ? Theme.signedPct(shown, decimals)
        : Theme.fixed(shown, decimals)
}
