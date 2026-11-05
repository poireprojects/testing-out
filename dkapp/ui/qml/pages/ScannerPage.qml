import QtQuick
import QtQuick.Layouts
import QtQuick.Controls.Basic
import Dk 1.0
import DkCharts 1.0

Item {
    id: page
    readonly property var st: app.state
    readonly property var sc: st.scanner || ({})
    property string selected: ""
    property var selRow: ({})

    function pickDefault() {
        if (page.selected === "" && app.scannerModel.count > 0) select(app.scannerModel.get(0))
    }
    function select(row) {
        page.selected = row.symbol
        page.selRow = row
        app.selectSymbol(row.symbol)
    }
    Connections {
        target: app.scannerModel
        function onCountChanged() { page.pickDefault() }
        function onDataChanged() {
            for (var i = 0; i < app.scannerModel.count; i++) {
                var r = app.scannerModel.get(i)
                if (r.symbol === page.selected) { page.selRow = r; break }
            }
        }
    }
    Component.onCompleted: pickDefault()

    function explainRow(r) {
        var out = []
        if (!r || !r.symbol) return out
        if (r.distVwap > 0.05) out.push({ t: "trading " + Theme.fixed(r.distVwap, 2) + "% above today's average price (VWAP) — buyers have the upper hand.", g: 1 })
        else if (r.distVwap < -0.05) out.push({ t: "trading " + Theme.fixed(-r.distVwap, 2) + "% below VWAP — sellers have the upper hand.", g: -1 })
        if (r.rvol >= 1.5) out.push({ t: "volume is " + Theme.fixed(r.rvol, 1) + "× a normal day so far — unusual interest.", g: 1 })
        else if (r.rvol > 0) out.push({ t: "volume is " + Theme.fixed(r.rvol, 1) + "× normal — nothing special.", g: 0 })
        if (r.rsi >= 75) out.push({ t: "RSI " + Theme.fixed(r.rsi, 0) + " — it ran up fast and may be stretched.", g: -1 })
        else if (r.rsi >= 55) out.push({ t: "RSI " + Theme.fixed(r.rsi, 0) + " — healthy upward strength.", g: 1 })
        else if (r.rsi >= 0 && r.rsi < 40) out.push({ t: "RSI " + Theme.fixed(r.rsi, 0) + " — weak right now.", g: -1 })
        if (r.spreadBps >= 0) out.push({ t: "spread " + Theme.fixed(r.spreadBps, 1) + " bps (" + Theme.money(20 * r.spreadBps / 10000, 3) + " on a $20 trade).", g: r.spreadBps < 10 ? 1 : 0 })
        if (r.flags) out.push({ t: "skipped: " + r.flags, g: -1 })
        return out
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 28
        anchors.topMargin: 4
        spacing: Theme.gap

        // stat strip
        RowLayout {
            Layout.fillWidth: true
            spacing: 10
            Repeater {
                model: [
                    { l: "universe", v: String(sc.universeSize || 0), tip: "" },
                    { l: "up close", v: String(sc.hotCount || 0), tip: "" },
                    { l: "setups today", v: String(sc.signalsToday || 0), tip: "" },
                    { l: "full scan", v: Theme.agoSecs(sc.fullScanAge), tip: "" },
                    { l: "close-up scan", v: Theme.agoSecs(sc.hotScanAge), tip: "" },
                    { l: "market (SPY)", v: sc.marketTrendUp === true ? "above vwap" : sc.marketTrendUp === false ? "below vwap" : "—", tip: "vwap" }
                ]
                delegate: Rectangle {
                    implicitHeight: 54
                    Layout.fillWidth: true
                    radius: 14
                    color: Theme.surface
                    border.color: Theme.border
                    ColumnLayout {
                        anchors.fill: parent
                        anchors.leftMargin: 14
                        anchors.topMargin: 8
                        anchors.bottomMargin: 8
                        spacing: 1
                        RowLayout { spacing: 4; SectionLabel { text: modelData.l; font.pixelSize: 10 } InfoTip { term: modelData.tip; visible: modelData.tip !== "" } }
                        Txt { text: modelData.v; font.family: Theme.display; font.pixelSize: 16; font.weight: Font.Medium }
                    }
                }
            }
        }
        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            Icon { name: "data"; size: 14; color: st.isDemo ? Theme.amber : Theme.textMuted }
            Txt { text: "data: " + (st.feedLabel || "—"); color: st.isDemo ? Theme.amber : Theme.textMuted; font.pixelSize: 12 }
            InfoTip { text: st.coverageNote || "" }
            Item { Layout.fillWidth: true }
            Txt { text: "click a row to see its chart"; color: Theme.textMuted; font.pixelSize: 12 }
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: Theme.gap

            // ---------------- ranked list
            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                radius: Theme.radius
                color: Theme.surface
                border.color: Theme.border
                clip: true

                ColumnLayout {
                    anchors.fill: parent
                    spacing: 0
                    // header
                    RowLayout {
                        Layout.fillWidth: true
                        Layout.leftMargin: 18
                        Layout.rightMargin: 18
                        Layout.topMargin: 14
                        Layout.bottomMargin: 10
                        spacing: 0
                        HeaderCell { w: 30; text: "#" }
                        HeaderCell { w: 84; text: "stock" }
                        HeaderCell { w: 76; text: "price"; alignRight: true }
                        HeaderCell { w: 72; text: "today"; alignRight: true }
                        HeaderCell { w: 70; text: "rel vol"; alignRight: true; tip: "relative volume" }
                        HeaderCell { w: 52; text: "rsi"; alignRight: true; tip: "rsi" }
                        HeaderCell { w: 80; text: "vs vwap"; alignRight: true; tip: "vwap" }
                        HeaderCell { w: 72; text: "trend"; alignRight: true; visible: list.width > 720 }
                        HeaderCell { w: 76; text: "interest"; alignRight: true }
                        Item { Layout.fillWidth: true }
                        HeaderCell { w: 118; text: "decision"; alignRight: true }
                    }
                    Rectangle { Layout.fillWidth: true; height: 1; color: Theme.border }
                    ListView {
                        id: list
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        model: app.scannerModel
                        boundsBehavior: Flickable.StopAtBounds
                        ScrollBar.vertical: ScrollBar {
                            id: vsb
                            contentItem: Rectangle { implicitWidth: 6; radius: 3; color: Theme.borderStrong; opacity: vsb.active ? 1 : 0.5 }
                        }
                        move: Transition { NumberAnimation { properties: "y"; duration: 380; easing.type: Easing.OutCubic } }
                        moveDisplaced: Transition { NumberAnimation { properties: "y"; duration: 380; easing.type: Easing.OutCubic } }
                        add: Transition { NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 250 } }
                        displaced: Transition { NumberAnimation { properties: "y"; duration: 300; easing.type: Easing.OutCubic } }
                        delegate: Item {
                            id: rowItem
                            width: list.width
                            height: 48
                            readonly property bool sel: model.symbol === page.selected
                            Rectangle {
                                anchors.fill: parent
                                anchors.leftMargin: 6
                                anchors.rightMargin: 6
                                radius: 10
                                color: rowItem.sel ? Theme.surface2 : (rma.containsMouse ? Qt.rgba(1, 1, 1, 0.025) : "transparent")
                            }
                            Rectangle {
                                visible: model.hot
                                width: 3; height: 22; radius: 1.5
                                anchors.verticalCenter: parent.verticalCenter
                                x: 8
                                color: model.signal === "buy" ? Theme.lime : Theme.borderStrong
                            }
                            RowLayout {
                                anchors.fill: parent
                                anchors.leftMargin: 18
                                anchors.rightMargin: 18
                                spacing: 0
                                Txt { text: index + 1; color: Theme.textMuted; font.pixelSize: 12; Layout.preferredWidth: 30; Layout.minimumWidth: 30; Layout.maximumWidth: 30 }
                                RowLayout {
                                    Layout.preferredWidth: 84; Layout.minimumWidth: 84; Layout.maximumWidth: 84
                                    spacing: 6
                                    Txt { text: model.symbol; font.family: Theme.display; font.pixelSize: 15; font.weight: Font.Bold; opacity: model.eligible ? 1 : 0.45 }
                                    Rectangle {
                                        width: 6; height: 6; radius: 3
                                        color: model.dataAge < 30 ? Theme.lime : model.dataAge < 90 ? Theme.amber : Theme.coral
                                        ToolTip.visible: dma.containsMouse
                                        ToolTip.text: "price updated " + Theme.agoSecs(model.dataAge)
                                        MouseArea { id: dma; anchors.fill: parent; anchors.margins: -4; hoverEnabled: true }
                                    }
                                }
                                Txt { text: Theme.money(model.price); font.pixelSize: 13; Layout.preferredWidth: 76; Layout.minimumWidth: 76; Layout.maximumWidth: 76; horizontalAlignment: Text.AlignRight }
                                Txt { text: Theme.signedPct(model.changePct); color: Theme.pnlColor(model.changePct); font.pixelSize: 13; Layout.preferredWidth: 72; Layout.minimumWidth: 72; Layout.maximumWidth: 72; horizontalAlignment: Text.AlignRight }
                                Txt { text: model.rvol > 0 ? Theme.fixed(model.rvol, 1) + "×" : "—"; color: model.rvol >= 1.5 ? Theme.lime : Theme.text; font.pixelSize: 13; Layout.preferredWidth: 70; Layout.minimumWidth: 70; Layout.maximumWidth: 70; horizontalAlignment: Text.AlignRight }
                                Txt { text: model.rsi >= 0 ? Theme.fixed(model.rsi, 0) : "—"; color: model.rsi >= 75 ? Theme.amber : Theme.text; font.pixelSize: 13; Layout.preferredWidth: 52; Layout.minimumWidth: 52; Layout.maximumWidth: 52; horizontalAlignment: Text.AlignRight }
                                Txt { text: model.hot ? Theme.signedPct(model.distVwap) : "—"; color: model.hot ? Theme.pnlColor(model.distVwap) : Theme.textMuted; font.pixelSize: 13; Layout.preferredWidth: 80; Layout.minimumWidth: 80; Layout.maximumWidth: 80; horizontalAlignment: Text.AlignRight }
                                Item {
                                    visible: list.width > 720
                                    Layout.preferredWidth: 72; Layout.minimumWidth: 72; Layout.maximumWidth: 72
                                    implicitHeight: 24
                                    Sparkline { anchors.right: parent.right; width: 60; height: 22; values: model.spark || []; visible: (model.spark || []).length > 1 }
                                }
                                Item {
                                    Layout.preferredWidth: 76; Layout.minimumWidth: 76; Layout.maximumWidth: 76
                                    implicitHeight: 10
                                    ThinBar { anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; width: 60; value: model.score; barColor: model.hot ? Theme.lime : Theme.textMuted }
                                }
                                Item { Layout.fillWidth: true }
                                Item {
                                    Layout.preferredWidth: 118; Layout.minimumWidth: 118; Layout.maximumWidth: 118
                                    implicitHeight: 24
                                    Row {
                                        anchors.right: parent.right
                                        anchors.verticalCenter: parent.verticalCenter
                                        spacing: 6
                                        // one clear chip: BUY setup > AI/risk verdict > WATCH
                                        ActionChip { visible: model.signal === "buy" || (model.signal === "watch" && model.aiVerdict === ""); action: model.signal }
                                        Pill { visible: model.signal !== "buy" && model.aiVerdict !== ""; text: model.aiVerdict; kind: model.aiVerdict === "ai: buy" ? "buy" : model.aiVerdict === "blocked" || model.aiVerdict === "ai offline" || model.aiVerdict === "ai error" ? "warn" : "neutral" }
                                        Txt { visible: model.signal === "" && model.aiVerdict === "" && !model.eligible; text: "filtered"; color: Theme.textMuted; font.pixelSize: 12 }
                                    }
                                }
                            }
                            MouseArea { id: rma; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: page.select(app.scannerModel.get(index)) }
                        }
                    }
                }
                EmptyState {
                    anchors.centerIn: parent
                    visible: app.scannerModel.count === 0
                    icon: "moon"
                    title: st.session && st.session.isOpen ? "first scan is on its way…" : "the market is closed — stocks are sleeping."
                    message: st.session && st.session.isOpen ? "pulling prices for the whole list." : "the scanner wakes up automatically at 9:30 ET on trading days."
                }
            }

            // ---------------- detail
            Rectangle {
                Layout.preferredWidth: Math.max(340, Math.min(440, page.width * 0.29))
                Layout.fillHeight: true
                radius: Theme.radius
                color: Theme.surface
                border.color: Theme.border
                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 20
                    spacing: 12
                    RowLayout {
                        Txt { text: page.selected || "—"; font.family: Theme.display; font.pixelSize: 26; font.weight: Font.Bold }
                        Item { Layout.fillWidth: true }
                        ColumnLayout {
                            spacing: 0
                            Txt { text: Theme.money(page.selRow.price); font.family: Theme.display; font.pixelSize: 18; Layout.alignment: Qt.AlignRight }
                            Txt { text: Theme.signedPct(page.selRow.changePct) + " today"; color: Theme.pnlColor(page.selRow.changePct); font.pixelSize: 12; Layout.alignment: Qt.AlignRight }
                        }
                    }
                    RowLayout {
                        spacing: 6
                        ActionChip { visible: (page.selRow.signal || "") !== ""; action: page.selRow.signal || "" }
                        Pill { visible: (page.selRow.strategy || "") !== ""; text: page.selRow.strategy || ""; kind: "info" }
                        Pill { visible: (page.selRow.aiVerdict || "") !== ""; text: page.selRow.aiVerdict || ""; kind: "neutral" }
                    }
                    CandleChart {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 260
                        bars: app.chart.symbol === page.selected ? (app.chart.bars || []) : []
                        markers: app.chart.symbol === page.selected ? (app.chart.markers || []) : []
                        textColor: Theme.textMuted
                        fontFamily: Theme.body
                        upColor: Theme.lime
                        downColor: Theme.coral
                        Txt {
                            anchors.centerIn: parent
                            visible: !(app.chart.bars && app.chart.bars.length > 1 && app.chart.symbol === page.selected)
                            text: page.selRow.hot ? "loading chart…" : "charts are shown for the stocks being watched up close"
                            color: Theme.textMuted
                            font.pixelSize: 12
                        }
                    }
                    SectionLabel { text: "in plain english" }
                    Repeater {
                        model: page.explainRow(page.selRow)
                        delegate: RowLayout {
                            Layout.fillWidth: true
                            spacing: 10
                            Rectangle { width: 6; height: 6; radius: 3; Layout.alignment: Qt.AlignTop; Layout.topMargin: 6; color: modelData.g > 0 ? Theme.lime : modelData.g < 0 ? Theme.coral : Theme.textMuted }
                            Txt { text: modelData.t; color: Theme.textDim; font.pixelSize: 13; wrapMode: Text.WordWrap; elide: Text.ElideNone; Layout.fillWidth: true }
                        }
                    }
                    Item { Layout.fillHeight: true }
                    Txt {
                        text: "a ranking is not a recommendation. trades only happen when a strategy fires, the AI agrees (if enabled) and every safety rule passes."
                        color: Theme.textMuted; font.pixelSize: 12; wrapMode: Text.WordWrap; elide: Text.ElideNone; Layout.fillWidth: true
                    }
                }
            }
        }
    }

    component HeaderCell: RowLayout {
        property real w: 80
        property alias text: lbl.text
        property bool alignRight: false
        property string tip: ""
        Layout.preferredWidth: w
        Layout.minimumWidth: w
        Layout.maximumWidth: w
        spacing: 4
        Item { Layout.fillWidth: parent.alignRight }
        SectionLabel { id: lbl; font.pixelSize: 10 }
        InfoTip { term: parent.tip; visible: parent.tip !== "" }
    }
}
