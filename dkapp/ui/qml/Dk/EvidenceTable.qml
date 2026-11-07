import QtQuick
import QtQuick.Layouts

// The research evidence table (one row per approach: trades, net per trade, 90% range, $ on $100, verdict).
ColumnLayout {
    id: root
    property var rows: []
    property string moneyLabel: "$ on $100"
    spacing: 0
    RowLayout {
        Layout.fillWidth: true
        spacing: 8
        SectionLabel { text: "approach"; font.pixelSize: 10; Layout.fillWidth: true }
        SectionLabel { text: "trades"; font.pixelSize: 10; Layout.preferredWidth: 64; horizontalAlignment: Text.AlignRight }
        SectionLabel { text: "per trade"; font.pixelSize: 10; Layout.preferredWidth: 84; horizontalAlignment: Text.AlignRight }
        SectionLabel { text: "90% range"; font.pixelSize: 10; Layout.preferredWidth: 150; horizontalAlignment: Text.AlignRight }
        SectionLabel { text: root.moneyLabel; font.pixelSize: 10; Layout.preferredWidth: 80; horizontalAlignment: Text.AlignRight }
        SectionLabel { text: "verdict"; font.pixelSize: 10; Layout.preferredWidth: 110; horizontalAlignment: Text.AlignRight }
    }
    Repeater {
        model: root.rows
        delegate: Rectangle {
            Layout.fillWidth: true
            implicitHeight: 34
            color: index % 2 === 0 ? "transparent" : Qt.rgba(1, 1, 1, 0.018)
            radius: 6
            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 4; anchors.rightMargin: 4
                spacing: 8
                Txt { text: modelData.label; font.pixelSize: 13; Layout.fillWidth: true; elide: Text.ElideRight
                      font.weight: modelData.key === "no_trade" ? Font.DemiBold : Font.Normal }
                Txt { text: modelData.trades.toLocaleString(Qt.locale("en_US"), 'f', 0); font.pixelSize: 13; color: Theme.textDim
                      Layout.preferredWidth: 64; horizontalAlignment: Text.AlignRight }
                Txt { text: modelData.trades > 0 ? Theme.signedPct(modelData.netPct, 3) : "—"; font.pixelSize: 13; font.weight: Font.DemiBold
                      color: modelData.trades === 0 ? Theme.textMuted : modelData.netPct > 0 ? Theme.lime : Theme.coral
                      Layout.preferredWidth: 84; horizontalAlignment: Text.AlignRight }
                Txt { text: modelData.trades > 0 ? Theme.signedPct(modelData.lo, 2) + " … " + Theme.signedPct(modelData.hi, 2) : (modelData.key === "no_trade" ? "no risk" : modelData.verdict === "benchmark" ? "buy and hold" : "took no trades")
                      font.pixelSize: 12; color: Theme.textMuted; Layout.preferredWidth: 150; horizontalAlignment: Text.AlignRight }
                Txt { text: Theme.signedMoney(modelData.pnl); font.pixelSize: 13
                      color: modelData.pnl < -0.005 ? Theme.coral : Theme.textDim
                      Layout.preferredWidth: 80; horizontalAlignment: Text.AlignRight }
                Item {
                    Layout.preferredWidth: 110; implicitHeight: 22
                    Pill {
                        anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                        text: modelData.verdict === "eligible_for_forward_test" ? "forward test" : modelData.verdict
                        kind: modelData.verdict === "rejected" ? "sell" : modelData.verdict === "insufficient" ? "warn" : "neutral"
                        fontSize: 10
                    }
                }
            }
        }
    }
}
