import QtQuick
import QtQuick.Layouts
import QtQuick.Controls.Basic
import Dk 1.0
import DkCharts 1.0

DkScroll {
    id: page
    readonly property var perf: app.performance || ({})
    readonly property var s: perf.stats || ({})
    readonly property var bt: app.backtest || ({})
    readonly property var btr: bt.results || null
    readonly property bool demo: app.state.isDemo === true || app.mode === "demo"
    property int btDays: 40

    ColumnLayout {
        width: parent.width
        spacing: Theme.gap

        Rectangle {
            Layout.fillWidth: true
            visible: page.demo
            implicitHeight: 44
            radius: 14
            color: Theme.amberSoft
            RowLayout {
                anchors.fill: parent; anchors.margins: 14; spacing: 10
                Icon { name: "flask"; size: 16; color: Theme.amber }
                Txt { text: "everything on this page comes from the demo simulator. it shows how the reports work — it is not evidence of real performance."; color: Theme.amber; font.pixelSize: 13; Layout.fillWidth: true }
            }
        }

        // KPI tiles
        GridLayout {
            Layout.fillWidth: true
            columns: 6
            columnSpacing: Theme.gap
            rowSpacing: Theme.gap
            StatTile { Layout.fillWidth: true; label: "net result"; tip: "realized"; valueText: Theme.signedMoney(page.s.total || 0); valueColor: Theme.pnlColor(page.s.total); sub: "after " + Theme.money(page.perf.feesTotal || 0) + " in fees" }
            StatTile { Layout.fillWidth: true; label: "win rate"; tip: "win rate"; valueText: Theme.pct((page.s.winRate || 0) * 100, 0); sub: (page.s.wins || 0) + " wins · " + (page.s.losses || 0) + " losses" }
            StatTile { Layout.fillWidth: true; label: "profit factor"; tip: "profit factor"; valueText: (page.s.count || 0) > 0 ? ((page.s.profitFactor || 0) >= 999 ? "∞" : Theme.fixed(page.s.profitFactor, 2)) : "—"; valueColor: (page.s.profitFactor || 0) >= 1 ? Theme.lime : Theme.text; sub: "over 1.0 = won more than lost" }
            StatTile { Layout.fillWidth: true; label: "max drawdown"; tip: "max drawdown"; valueText: Theme.money(page.perf.maxDrawdown || 0); valueColor: (page.perf.maxDrawdown || 0) > 0 ? Theme.coral : Theme.text; sub: Theme.pct(page.perf.maxDrawdownPct || 0) + " from peak" }
            StatTile { Layout.fillWidth: true; label: "average win / loss"; tip: "expectancy"; valueText: Theme.money(page.s.avgWin || 0) + " / " + Theme.money(Math.abs(page.s.avgLoss || 0)); sub: "per trade: " + Theme.signedMoney(page.s.expectancy || 0, 3) }
            StatTile { Layout.fillWidth: true; label: "vs. buying SPY"; tip: "benchmark"; valueText: page.perf.hasReturn ? Theme.signedPct(page.perf.totalReturnPct) : "—"; valueColor: Theme.pnlColor(page.perf.totalReturnPct); sub: page.perf.hasBenchmark ? "SPY: " + Theme.signedPct(page.perf.benchmarkReturnPct) : "needs 2+ trading days" }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.gap
            Card {
                Layout.fillWidth: true
                Layout.preferredWidth: 3; Layout.minimumWidth: 3; Layout.maximumWidth: 3
                title: "account growth"
                tip: "benchmark"
                subtitle: "end-of-day account value. the dashed line is what the same money would be worth in SPY (the S&P 500)."
                LineChart {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 260
                    visible: (page.perf.curve || []).length >= 2
                    values: (page.perf.curve || []).map(function(c) { return c.equity })
                    labels: (page.perf.curve || []).map(function(c) { return c.date.substring(5) })
                    compareValues: (page.perf.benchmarkCurve || []).length > 1 && page.perf.benchmarkCurve.length === (page.perf.curve || []).length ? page.perf.benchmarkCurve.map(function(c) { return c.value }) : []
                    compareLabel: "SPY"
                    seriesLabel: "account"
                    baselineValue: page.perf.startEquity || NaN
                    lineColor: Theme.lime
                    compareColor: Theme.sky
                    textColor: Theme.textMuted
                }
                EmptyState {
                    visible: (page.perf.curve || []).length < 2
                    Layout.fillWidth: true; Layout.topMargin: 30; Layout.bottomMargin: 30
                    icon: "performance"
                    title: "not enough history yet"
                    message: "the growth chart appears after two trading days. no projections, no made-up numbers — only what actually happened."
                }
            }
            Card {
                Layout.fillWidth: true
                Layout.preferredWidth: 2; Layout.minimumWidth: 2; Layout.maximumWidth: 2
                title: "daily results"
                subtitle: "profit or loss for each trading day"
                BarChart {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 260
                    visible: (page.perf.daily || []).length > 0
                    values: (page.perf.daily || []).map(function(d) { return d.pnl })
                    labels: (page.perf.daily || []).map(function(d) { return d.date })
                    textColor: Theme.textMuted
                }
                EmptyState {
                    visible: (page.perf.daily || []).length === 0
                    Layout.fillWidth: true; Layout.topMargin: 30; Layout.bottomMargin: 30
                    icon: "clock"; title: "no full days yet"; message: "each trading day gets a bar after the market closes."
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.gap
            // by strategy
            Card {
                Layout.fillWidth: true
                Layout.preferredWidth: 3; Layout.minimumWidth: 3; Layout.maximumWidth: 3
                Layout.alignment: Qt.AlignTop
                title: "by strategy"
                tip: "evidence"
                Repeater {
                    model: page.perf.byStrategy || []
                    delegate: RowLayout {
                        Layout.fillWidth: true
                        spacing: 12
                        Rectangle { width: 8; height: 8; radius: 4; color: modelData.enabled ? Theme.lime : Theme.textMuted }
                        Txt { text: modelData.name; font.pixelSize: 14; font.weight: Font.Medium; Layout.preferredWidth: 190; Layout.minimumWidth: 190; Layout.maximumWidth: 190 }
                        Txt { text: modelData.count + " trades"; color: Theme.textDim; font.pixelSize: 13; Layout.preferredWidth: 80; Layout.minimumWidth: 80; Layout.maximumWidth: 80 }
                        Txt { text: modelData.count > 0 ? Theme.pct(modelData.winRate * 100, 0) + " wins" : ""; color: Theme.textDim; font.pixelSize: 13; Layout.preferredWidth: 80; Layout.minimumWidth: 80; Layout.maximumWidth: 80 }
                        Txt { text: modelData.count > 0 ? Theme.signedMoney(modelData.total) : ""; color: Theme.pnlColor(modelData.total); font.pixelSize: 13; Layout.preferredWidth: 80; Layout.minimumWidth: 80; Layout.maximumWidth: 80 }
                        Item { Layout.fillWidth: true }
                        Pill {
                            text: modelData.evidence === "evidence" ? "EVIDENCE ✓" : modelData.evidence === "no_edge" ? "NO EDGE FOUND" : modelData.evidence === "insufficient" ? "TOO FEW TRADES" : modelData.evidence === "synthetic_only" ? "SYNTHETIC ONLY" : "UNTESTED"
                            kind: modelData.evidence === "evidence" ? "lime" : modelData.evidence === "no_edge" ? "sell" : modelData.evidence === "untested" ? "neutral" : "warn"
                        }
                    }
                }
                Txt {
                    text: "only strategies marked EVIDENCE ✓ (from real historical data) are allowed to trade real money. in paper mode, untested strategies can trade to gather forward-test evidence."
                    color: Theme.textMuted; font.pixelSize: 12; wrapMode: Text.WordWrap; elide: Text.ElideNone; Layout.fillWidth: true
                }
            }
            // ai helping + costs
            ColumnLayout {
                Layout.fillWidth: true
                Layout.preferredWidth: 2; Layout.minimumWidth: 2; Layout.maximumWidth: 2
                Layout.alignment: Qt.AlignTop
                spacing: Theme.gap
                Card {
                    Layout.fillWidth: true
                    title: "is the ai helping?"
                    tip: "shadow trades"
                    subtitle: "hypothetical results of setups the AI approved vs. skipped, using identical exit rules."
                    Repeater {
                        model: [
                            { k: "approved", d: (page.perf.shadow || {}).approved || {} },
                            { k: "skipped", d: (page.perf.shadow || {}).skipped || {} }
                        ]
                        delegate: RowLayout {
                            Layout.fillWidth: true
                            spacing: 10
                            ActionChip { action: modelData.k === "approved" ? "buy" : "skip"; Layout.preferredWidth: 70; Layout.minimumWidth: 70; Layout.maximumWidth: 70 }
                            Txt { text: (modelData.d.count || 0) + " setups"; color: Theme.textDim; font.pixelSize: 13; Layout.preferredWidth: 80; Layout.minimumWidth: 80; Layout.maximumWidth: 80 }
                            Txt { text: (modelData.d.count || 0) > 0 ? "avg " + Theme.signedPct(modelData.d.avgReturnPct, 3) : "—"; color: Theme.pnlColor(modelData.d.avgReturnPct); font.pixelSize: 13; Layout.fillWidth: true }
                            Txt { text: (modelData.d.count || 0) > 0 ? Theme.pct((modelData.d.winRate || 0) * 100, 0) + " win" : ""; color: Theme.textDim; font.pixelSize: 13 }
                        }
                    }
                    Txt {
                        readonly property var a: (page.perf.shadow || {}).approved || {}
                        readonly property var k: (page.perf.shadow || {}).skipped || {}
                        text: (a.count || 0) < 20 || (k.count || 0) < 20 ? "needs at least 20 of each before drawing any conclusion."
                            : (a.avgReturnPct > k.avgReturnPct ? "so far, approved setups did better than skipped ones." : "so far the AI's picks are NOT beating the ones it skipped — consider rules-only mode.")
                        color: Theme.textMuted; font.pixelSize: 12; wrapMode: Text.WordWrap; Layout.fillWidth: true
                    }
                }
                Card {
                    Layout.fillWidth: true
                    title: "operating costs"
                    icon: "coin"
                    readonly property var c: page.perf.costs || ({})
                    Repeater {
                        model: [
                            { k: "cloud AI this month", v: Theme.money(((page.perf.costs || {}).aiMonth || 0), 3) + " · " + ((page.perf.costs || {}).aiCallsMonth || 0) + " calls" },
                            { k: "regulatory fees this month", v: Theme.money((page.perf.costs || {}).feesMonth || 0) },
                            { k: "market data subscription", v: Theme.money((page.perf.costs || {}).dataMonthly || 0) + " / month" },
                            { k: "estimated monthly total", v: Theme.money((page.perf.costs || {}).projectedMonthly || 0) }
                        ]
                        delegate: RowLayout {
                            Layout.fillWidth: true
                            Txt { text: modelData.k; color: Theme.textDim; font.pixelSize: 13 }
                            Item { Layout.fillWidth: true }
                            Txt { text: modelData.v; font.pixelSize: 13; font.weight: index === 3 ? Font.DemiBold : Font.Normal }
                        }
                    }
                }
            }
        }

        // ---------------- strategy lab
        Card {
            Layout.fillWidth: true
            title: "strategy lab — backtests"
            icon: "flask"
            tip: "backtest"
            subtitle: page.demo ? "in demo mode this uses synthetic prices. it tests the machinery, but can never count as evidence. connect Alpaca to test on real history."
                                : "replays each strategy on real 1-minute history with spread, slippage and fees. the last 40% of days are held out as an unseen test."
            headerRight: [
                Segmented { small: true; options: [{ value: 20, label: "20 days" }, { value: 40, label: "40 days" }, { value: 60, label: "60 days" }]; current: page.btDays; onPicked: (v) => page.btDays = v },
                DkButton { compact: true; kind: "primary"; iconName: "flask"; text: page.bt.running ? "running…" : "run backtest"; enabled: !page.bt.running; onClicked: app.runBacktest(page.btDays, 30) }
            ]
            ColumnLayout {
                visible: page.bt.running === true
                Layout.fillWidth: true
                spacing: 6
                ThinBar { Layout.fillWidth: true; value: page.bt.progress || 0 }
                Txt { text: page.bt.message || ""; color: Theme.textMuted; font.pixelSize: 12 }
            }
            Txt {
                visible: !page.bt.running && (page.bt.message || "").indexOf("failed") >= 0
                text: page.bt.message || ""
                color: Theme.coral; font.pixelSize: 13; wrapMode: Text.WordWrap; Layout.fillWidth: true
            }
            ColumnLayout {
                visible: page.btr !== null && !page.bt.running
                Layout.fillWidth: true
                spacing: 8
                RowLayout {
                    spacing: 8
                    Pill { text: page.btr ? page.btr.source : ""; kind: page.btr && page.btr.source.indexOf("synthetic") >= 0 ? "warn" : "info" }
                    Txt { text: page.btr ? (page.btr.days + " days · " + page.btr.firstDay + " → " + page.btr.lastDay + " · unseen test from " + page.btr.splitDay) : ""; color: Theme.textMuted; font.pixelSize: 12 }
                }
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 0
                    SectionLabel { text: "strategy"; font.pixelSize: 10; Layout.preferredWidth: 190; Layout.minimumWidth: 190; Layout.maximumWidth: 190 }
                    SectionLabel { text: "trades (test)"; font.pixelSize: 10; Layout.preferredWidth: 100; Layout.minimumWidth: 100; Layout.maximumWidth: 100 }
                    SectionLabel { text: "avg/trade (train)"; font.pixelSize: 10; Layout.preferredWidth: 120; Layout.minimumWidth: 120; Layout.maximumWidth: 120 }
                    SectionLabel { text: "avg/trade (test)"; font.pixelSize: 10; Layout.preferredWidth: 120; Layout.minimumWidth: 120; Layout.maximumWidth: 120 }
                    SectionLabel { text: "random entries"; font.pixelSize: 10; Layout.preferredWidth: 110; Layout.minimumWidth: 110; Layout.maximumWidth: 110 }
                    SectionLabel { text: "p-value"; font.pixelSize: 10; Layout.preferredWidth: 70; Layout.minimumWidth: 70; Layout.maximumWidth: 70 }
                    SectionLabel { text: "verdict"; font.pixelSize: 10; Layout.fillWidth: true }
                }
                Repeater {
                    model: page.btr ? Object.keys(page.btr.strategies) : []
                    delegate: ColumnLayout {
                        readonly property var r: page.btr.strategies[modelData]
                        Layout.fillWidth: true
                        spacing: 3
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 0
                            Txt { text: r.name; font.pixelSize: 14; font.weight: Font.Medium; Layout.preferredWidth: 190; Layout.minimumWidth: 190; Layout.maximumWidth: 190 }
                            Txt { text: r.outOfSample.trades; font.pixelSize: 13; Layout.preferredWidth: 100; Layout.minimumWidth: 100; Layout.maximumWidth: 100 }
                            Txt { text: Theme.signedPct(r.inSample.avgRetPct, 3); color: Theme.pnlColor(r.inSample.avgRetPct); font.pixelSize: 13; Layout.preferredWidth: 120; Layout.minimumWidth: 120; Layout.maximumWidth: 120 }
                            Txt { text: Theme.signedPct(r.outOfSample.avgRetPct, 3); color: Theme.pnlColor(r.outOfSample.avgRetPct); font.pixelSize: 13; Layout.preferredWidth: 120; Layout.minimumWidth: 120; Layout.maximumWidth: 120 }
                            Txt { text: Theme.signedPct(r.randomBaseline.avgRetPct, 3); color: Theme.textDim; font.pixelSize: 13; Layout.preferredWidth: 110; Layout.minimumWidth: 110; Layout.maximumWidth: 110 }
                            Txt { text: Theme.fixed(r.outOfSample.pValue, 2); font.pixelSize: 13; Layout.preferredWidth: 70; Layout.minimumWidth: 70; Layout.maximumWidth: 70 }
                            Pill {
                                text: r.verdict === "evidence" ? (page.btr.source.indexOf("synthetic") >= 0 ? "SYNTHETIC ONLY" : "EVIDENCE ✓") : r.verdict === "no_edge" ? "NO EDGE" : "TOO FEW TRADES"
                                kind: r.verdict === "evidence" ? "lime" : r.verdict === "no_edge" ? "sell" : "warn"
                            }
                            Item { Layout.fillWidth: true }
                        }
                        Txt { text: r.explanation; color: Theme.textMuted; font.pixelSize: 12; wrapMode: Text.WordWrap; elide: Text.ElideNone; Layout.fillWidth: true; Layout.bottomMargin: 6 }
                    }
                }
                Txt {
                    text: "\"avg/trade\" is the average result per trade after costs. \"random entries\" uses the same stops, targets and holding times but random entry times — a strategy has to beat that to matter. past results don't guarantee future ones."
                    color: Theme.textMuted; font.pixelSize: 12; wrapMode: Text.WordWrap; elide: Text.ElideNone; Layout.fillWidth: true
                }
            }
            Txt {
                visible: page.btr === null && !page.bt.running
                text: "no backtest yet. run one to see whether any strategy has shown an edge after costs."
                color: Theme.textMuted; font.pixelSize: 13
            }
        }
    }
}
