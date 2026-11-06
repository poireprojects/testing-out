import QtQuick
import QtQuick.Layouts
import QtQuick.Controls.Basic
import QtQuick.Shapes
import Dk 1.0
import DkCharts 1.0

DkScroll {
    id: page
    signal navigate(int page)
    readonly property var st: app.state
    readonly property var acct: st.account || ({})
    readonly property var lim: st.limits || ({})
    readonly property var status: st.status || ({})
    readonly property var positions: st.positions || []
    readonly property bool notConfigured: st.notConfigured !== undefined && st.notConfigured !== ""

    ColumnLayout {
        width: parent.width
        spacing: Theme.gap

        // ---------- not connected notice
        Card {
            visible: page.notConfigured
            Layout.fillWidth: true
            title: "connect your brokerage"
            icon: "plug"
            subtitle: page.st.notConfigured || ""
            RowLayout {
                DkButton { text: "open settings"; kind: "primary"; iconName: "arrowRight"; onClicked: page.navigate(5) }
                DkButton { text: "back to demo mode"; kind: "secondary"; onClicked: app.setMode("demo", true) }
            }
        }

        // ---------- hero row
        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.gap

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredWidth: 2
                implicitHeight: 300
                radius: Theme.radius
                color: Theme.surface
                border.color: Theme.border
                clip: true
                // soft lime glow in the corner
                Shape {
                    id: glow
                    anchors.fill: parent
                    preferredRendererType: Shape.CurveRenderer
                    ShapePath {
                        strokeWidth: -1
                        fillGradient: RadialGradient {
                            centerX: glow.width * 0.92; centerY: 0
                            centerRadius: 360; focalX: centerX; focalY: centerY
                            GradientStop { position: 0.0; color: Qt.rgba(0.784, 1, 0.235, (acct.dayPnl || 0) >= 0 ? 0.10 : 0.0) }
                            GradientStop { position: 1.0; color: Qt.rgba(0.784, 1, 0.235, 0.0) }
                        }
                        PathRectangle { x: 0; y: 0; width: glow.width; height: glow.height; radius: Theme.radius }
                    }
                }
                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 24
                    spacing: 4
                    RowLayout {
                        spacing: 6
                        Txt { text: "account value"; color: Theme.textDim; font.pixelSize: 13; font.weight: Font.Medium }
                        InfoTip { term: "account value" }
                        Item { Layout.fillWidth: true }
                        Pill { visible: st.isDemo === true; text: "DEMO DATA"; kind: "warn" }
                        Pill { visible: app.mode === "paper"; text: "PAPER MONEY"; kind: "info" }
                        Pill { visible: app.mode === "live"; text: "REAL MONEY"; kind: "live" }
                    }
                    MoneyText {
                        value: acct.equity || 0
                        font.pixelSize: 54
                        font.weight: Font.Medium
                        font.letterSpacing: -1.5
                    }
                    RowLayout {
                        spacing: 10
                        Rectangle {
                            implicitHeight: 30
                            implicitWidth: pnlRow.implicitWidth + 20
                            radius: 15
                            color: (acct.dayPnl || 0) >= 0 ? Theme.limeSoft : Theme.coralSoft
                            RowLayout {
                                id: pnlRow
                                anchors.centerIn: parent
                                spacing: 5
                                Icon { name: (acct.dayPnl || 0) >= 0 ? "up" : "down"; size: 14; stroke: 2.2; color: Theme.pnlColor(acct.dayPnl || 0.0000001) }
                                MoneyText { value: acct.dayPnl || 0; format: "signedMoney"; font.pixelSize: 14; font.weight: Font.DemiBold; color: Theme.pnlColor(acct.dayPnl) }
                                Txt { text: "(" + Theme.signedPct(acct.dayPnlPct || 0) + ")"; font.pixelSize: 13; color: Theme.pnlColor(acct.dayPnl) }
                            }
                        }
                        Txt { text: "today"; color: Theme.textMuted; font.pixelSize: 13 }
                        InfoTip { term: "today's p/l" }
                    }
                    LineChart {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        Layout.topMargin: 8
                        values: (st.equityToday || []).map(function(p) { return p.v })
                        labels: (st.equityToday || []).map(function(p) { return p.t })
                        baselineValue: acct.startEquity || NaN
                        lineColor: (acct.dayPnl || 0) >= 0 ? Theme.lime : Theme.coral
                        textColor: Theme.textMuted
                        fontFamily: Theme.body
                        seriesLabel: "value"
                        visible: (st.equityToday || []).length >= 2
                    }
                    Item {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        visible: (st.equityToday || []).length < 2
                        Txt {
                            anchors.centerIn: parent
                            text: st.session && st.session.isOpen ? "today's chart appears after a couple of minutes of trading" : "today's chart starts when the market opens"
                            color: Theme.textMuted
                            font.pixelSize: 13
                        }
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredWidth: 1
                implicitHeight: 300
                radius: Theme.radius
                color: Theme.surface
                border.color: Theme.border
                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 24
                    spacing: 12
                    RowLayout {
                        spacing: 8
                        StatusDot {
                            color: page.status.kind === "trading" ? Theme.lime : page.status.kind === "error" || page.status.kind === "stopped" ? Theme.coral : page.status.kind === "paused" ? Theme.amber : Theme.textMuted
                            pulse: page.status.kind === "trading"
                        }
                        SectionLabel { text: "what's happening" }
                    }
                    Txt {
                        text: page.status.headline || "starting up…"
                        font.family: Theme.display
                        font.pixelSize: 22
                        font.weight: Font.Medium
                        wrapMode: Text.WordWrap
                        elide: Text.ElideNone
                        Layout.fillWidth: true
                        lineHeight: 1.1
                    }
                    Txt {
                        text: page.status.detail || ""
                        color: Theme.textDim
                        font.pixelSize: 13
                        wrapMode: Text.WordWrap
                        elide: Text.ElideNone
                        Layout.fillWidth: true
                        lineHeight: 1.25
                    }
                    Item { Layout.fillHeight: true }
                    Rectangle { Layout.fillWidth: true; height: 1; color: Theme.border }
                    GridLayout {
                        columns: 2
                        columnSpacing: 12
                        rowSpacing: 7
                        Layout.fillWidth: true
                        Txt { text: "watching"; color: Theme.textMuted; font.pixelSize: 12 }
                        Txt { text: (st.scanner ? st.scanner.universeSize : 0) + " stocks · " + (st.scanner ? st.scanner.hotCount : 0) + " up close"; font.pixelSize: 12; Layout.fillWidth: true; horizontalAlignment: Text.AlignRight }
                        Txt { text: "last scan"; color: Theme.textMuted; font.pixelSize: 12 }
                        Txt { text: st.scanner ? Theme.agoSecs(st.scanner.fullScanAge) : "—"; font.pixelSize: 12; Layout.fillWidth: true; horizontalAlignment: Text.AlignRight }
                        Txt { text: "ai"; color: Theme.textMuted; font.pixelSize: 12 }
                        Txt {
                            text: st.ai ? (st.ai.decisionMode === "rules" ? "rules-only (ai not used)" : (st.ai.provider + " · " + st.ai.model)) : "—"
                            font.pixelSize: 12; Layout.fillWidth: true; horizontalAlignment: Text.AlignRight
                            color: st.ai && st.ai.healthy === false && st.ai.decisionMode !== "rules" ? Theme.coral : Theme.text
                        }
                    }
                    DkButton {
                        visible: st.ai && st.ai.decisionMode !== "rules" && st.ai.healthy === false
                        compact: true
                        kind: "secondary"
                        text: "use rules-only for now"
                        Layout.fillWidth: true
                        onClicked: app.setSetting("ai.decision_mode", "rules")
                    }
                }
            }
        }

        // ---------- stat tiles
        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.gap
            StatTile {
                Layout.fillWidth: true
                label: "settled cash"
                tip: "settled cash"
                icon: "coin"
                valueText: Theme.money(acct.settledCash || 0)
                sub: "buying power " + Theme.money(acct.buyingPower || 0)
            }
            StatTile {
                Layout.fillWidth: true
                label: "realized today"
                tip: "realized"
                valueText: Theme.signedMoney(acct.realizedToday || 0)
                valueColor: Theme.pnlColor(acct.realizedToday)
                sub: "from closed trades"
            }
            StatTile {
                Layout.fillWidth: true
                label: "unrealized"
                tip: "unrealized"
                valueText: Theme.signedMoney(acct.unrealized || 0)
                valueColor: Theme.pnlColor(acct.unrealized)
                sub: "on open positions"
            }
            StatTile {
                Layout.fillWidth: true
                label: "open positions"
                tip: "position"
                valueText: page.positions.length + " / " + (lim.maxPositions || 0)
                sub: "max " + Theme.money(lim.maxPosition || 0) + " each"
            }
            StatTile {
                Layout.fillWidth: true
                label: "daily loss limit"
                tip: "daily loss limit"
                valueText: Theme.money(lim.dailyLossUsed || 0, 2)
                sub: "used of " + Theme.money(lim.dailyLossLimit || 0)
                subColor: (lim.dailyLossUsed || 0) >= (lim.dailyLossLimit || 1) ? Theme.coral : Theme.textMuted
                Ring {
                    anchors.right: parent.right
                    anchors.top: parent.top
                    anchors.margins: 16
                    width: 38; height: 38
                    thickness: 5
                    value: (lim.dailyLossLimit || 0) > 0 ? (lim.dailyLossUsed || 0) / lim.dailyLossLimit : 0
                    color: value > 0.75 ? Theme.coral : value > 0.4 ? Theme.amber : Theme.lime
                    trackColor: Theme.surface3
                    Behavior on value { NumberAnimation { duration: 600; easing.type: Easing.OutCubic } }
                }
            }
        }

        // ---------- positions + rules
        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.gap

            Card {
                Layout.fillWidth: true
                Layout.preferredWidth: 2
                Layout.alignment: Qt.AlignTop
                title: "open positions"
                tip: "position"
                headerRight: [
                    DkButton { compact: true; kind: "ghost"; text: "all trades →"; onClicked: page.navigate(3) }
                ]
                EmptyState {
                    visible: page.positions.length === 0
                    Layout.fillWidth: true
                    Layout.topMargin: 14
                    Layout.bottomMargin: 14
                    icon: "eye"
                    title: "no positions right now"
                    message: "patience is a position too. when a setup passes every check, it'll show up here."
                }
                Repeater {
                    model: app.positionsModel
                    delegate: PositionRow { Layout.fillWidth: true }
                }
            }

            Card {
                Layout.fillWidth: true
                Layout.preferredWidth: 1
                Layout.alignment: Qt.AlignTop
                title: "your safety rules"
                tip: "risk engine"
                subtitle: "checked on every trade. the AI can't override them."
                Repeater {
                    model: [
                        { k: "max per trade", v: Theme.money(page.lim.maxPosition || 0) + (page.lim.compounding === "reinvest" ? " (grows with its own profits)" : " (fixed)") },
                        { k: "positions at once", v: String(page.lim.maxPositions || 0) },
                        { k: "daily loss limit", v: Theme.money(page.lim.dailyLossLimit || 0) },
                        { k: "risk per trade", v: Theme.money(page.lim.riskPerTrade || 0) + " if the stop hits" },
                        { k: "never", v: "margin · leverage · shorting · options · crypto" }
                    ]
                    delegate: RowLayout {
                        Layout.fillWidth: true
                        spacing: 10
                        Icon { name: "check"; size: 14; color: Theme.lime; stroke: 2.2 }
                        Txt { text: modelData.k; color: Theme.textDim; font.pixelSize: 13 }
                        Item { Layout.fillWidth: true }
                        Txt { text: modelData.v; font.pixelSize: 13; horizontalAlignment: Text.AlignRight; Layout.maximumWidth: 210; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                    }
                }
                DkButton { compact: true; kind: "ghost"; text: "change safety rules →"; onClicked: page.navigate(5) }
            }
        }

        // ---------- recent activity
        Card {
            Layout.fillWidth: true
            title: "latest activity"
            icon: "sparkle"
            headerRight: [ DkButton { compact: true; kind: "ghost"; text: "everything →"; onClicked: page.navigate(2) } ]
            EmptyState {
                visible: app.activityModel.count === 0
                Layout.fillWidth: true
                icon: "clock"
                title: "quiet so far"
                message: "events like scans, AI reviews and trades will appear here."
            }
            Repeater {
                model: app.activityModel
                delegate: EventRow {
                    visible: index < 7
                    Layout.fillWidth: true
                }
            }
        }
    }

    component PositionRow: Rectangle {
        radius: 14
        color: Theme.surface2
        implicitHeight: 74
        RowLayout {
            anchors.fill: parent
            anchors.margins: 14
            spacing: 14
            ColumnLayout {
                spacing: 2
                Layout.preferredWidth: 120
                RowLayout {
                    spacing: 6
                    Txt { text: model.symbol; font.family: Theme.display; font.pixelSize: 17; font.weight: Font.Bold }
                    Pill { text: model.status === "closing" ? "SELLING" : model.status === "pending_entry" ? "BUYING" : "HOLD"; kind: model.status === "closing" ? "sell" : model.status === "pending_entry" ? "buy" : "hold"; fontSize: 10 }
                }
                Txt { text: model.strategy; color: Theme.textMuted; font.pixelSize: 12 }
            }
            ColumnLayout {
                spacing: 2
                Layout.preferredWidth: 150
                Txt { text: Theme.fixed(model.qty, 4) + " sh @ " + Theme.money(model.entryPrice); font.pixelSize: 13; color: Theme.textDim }
                Txt { text: "now " + Theme.money(model.price) + " · " + model.heldMinutes + "m"; font.pixelSize: 13 }
            }
            // stop ... target progress
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 4
                readonly property real span: Math.max(0.0001, model.target - model.stop)
                readonly property real pos: Math.max(0, Math.min(1, (model.price - model.stop) / span))
                Item {
                    Layout.fillWidth: true
                    implicitHeight: 10
                    Rectangle { anchors.verticalCenter: parent.verticalCenter; width: parent.width; height: 4; radius: 2
                        gradient: Gradient { orientation: Gradient.Horizontal
                            GradientStop { position: 0; color: Theme.coral }
                            GradientStop { position: 0.5; color: Theme.surface3 }
                            GradientStop { position: 1; color: Theme.lime } } }
                    Rectangle {
                        width: 10; height: 10; radius: 5; color: Theme.text
                        border.color: Theme.bg; border.width: 2
                        x: parent.parent.pos * (parent.width - width)
                        Behavior on x { NumberAnimation { duration: 500; easing.type: Easing.OutCubic } }
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    Txt { text: "stop " + Theme.money(model.stop); font.pixelSize: 11; color: Theme.coral }
                    Item { Layout.fillWidth: true }
                    Txt { text: model.protection; font.pixelSize: 11; color: model.protection === "broker stop order" ? Theme.textMuted : Theme.amber }
                    InfoTip { term: model.protection === "broker stop order" ? "broker stop" : "app-watched stop" }
                    Item { Layout.fillWidth: true }
                    Txt { text: "target " + Theme.money(model.target); font.pixelSize: 11; color: Theme.lime }
                }
            }
            ColumnLayout {
                spacing: 2
                Layout.preferredWidth: 96
                Txt { text: Theme.signedMoney(model.pnl); font.family: Theme.display; font.pixelSize: 16; font.weight: Font.Medium; color: Theme.pnlColor(model.pnl); Layout.alignment: Qt.AlignRight }
                Txt { text: Theme.signedPct(model.pnlPct); font.pixelSize: 12; color: Theme.pnlColor(model.pnl); Layout.alignment: Qt.AlignRight }
            }
            DkButton {
                compact: true; kind: "danger"; text: "sell"; visible: model.status === "open"
                onClicked: app.closePosition(model.tradeId)
                ToolTip.visible: hovered; ToolTip.text: "sell this position now at market"; ToolTip.delay: 300
            }
        }
    }

    component EventRow: RowLayout {
        spacing: 12
        Rectangle {
            width: 8; height: 8; radius: 4
            Layout.alignment: Qt.AlignTop
            Layout.topMargin: 5
            color: model.level === "error" ? Theme.coral : model.level === "warning" ? Theme.amber : model.level === "success" ? Theme.lime : Theme.textMuted
        }
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 1
            RowLayout {
                spacing: 8
                Txt { text: model.title; font.pixelSize: 13; font.weight: Font.Medium; Layout.fillWidth: true }
                Txt { text: model.timeText; color: Theme.textMuted; font.pixelSize: 11 }
            }
            Txt { text: model.message; visible: model.message !== ""; color: Theme.textDim; font.pixelSize: 12; Layout.fillWidth: true; maximumLineCount: 2; wrapMode: Text.WordWrap }
        }
    }
}
