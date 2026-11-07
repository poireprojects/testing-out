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
                Txt { text: "account results on this page come from the demo simulator — they show how the reports work, not real performance. the research card is real market history and is labeled as such."; wrapMode: Text.WordWrap; color: Theme.amber; font.pixelSize: 13; Layout.fillWidth: true }
            }
        }

        // ---------------- forward evidence: only market data that arrived after the research (never mixed with history)
        Card {
            id: fwdCard
            Layout.fillWidth: true
            readonly property var f: (app.state && app.state.forward) ? app.state.forward : ({})
            title: "forward evidence — new market data only"
            icon: "eye"
            subtitle: "every setup the app sees, taken or not, is recorded when it's seen and scored after the close against what "
                      + "actually happened (hypothetical fills, costs included). records can't be edited: each one is chained to the last."
            headerRight: [ Pill { text: fwdCard.f.chainIntact === false ? "record altered!" : "forward · shadow"; kind: fwdCard.f.chainIntact === false ? "sell" : "info" } ]
            Txt {
                visible: !(fwdCard.f.groups && fwdCard.f.groups.length > 0)
                Layout.fillWidth: true; wrapMode: Text.WordWrap
                text: page.demo ? "not collected in demo mode — it needs real market prices (paper or live)."
                                : "nothing yet. it starts on the next market day the app runs with real prices."
                color: Theme.textMuted; font.pixelSize: 13
            }
            ColumnLayout {
                visible: fwdCard.f.groups !== undefined && fwdCard.f.groups.length > 0
                Layout.fillWidth: true
                spacing: 0
                Txt {
                    text: (fwdCard.f.observations || 0) + " observations · " + (fwdCard.f.from || "") + " → " + (fwdCard.f.to || "")
                          + " · per trade = % of the $20 position, after costs, under each setup's own plan"
                    color: Theme.textDim; font.pixelSize: 12; Layout.bottomMargin: 6; Layout.fillWidth: true; wrapMode: Text.WordWrap
                }
                RowLayout {
                    Layout.fillWidth: true; spacing: 8
                    SectionLabel { text: "setup type"; font.pixelSize: 10; Layout.fillWidth: true }
                    SectionLabel { text: "decision"; font.pixelSize: 10; Layout.preferredWidth: 90 }
                    SectionLabel { text: "scored"; font.pixelSize: 10; Layout.preferredWidth: 60; horizontalAlignment: Text.AlignRight }
                    SectionLabel { text: "per trade"; font.pixelSize: 10; Layout.preferredWidth: 80; horizontalAlignment: Text.AlignRight }
                    SectionLabel { text: "90% range"; font.pixelSize: 10; Layout.preferredWidth: 140; horizontalAlignment: Text.AlignRight }
                }
                Repeater {
                    model: fwdCard.f.groups || []
                    delegate: RowLayout {
                        Layout.fillWidth: true; spacing: 8
                        Layout.preferredHeight: 28
                        Txt { text: modelData.hypothesis; font.pixelSize: 13; Layout.fillWidth: true; elide: Text.ElideRight }
                        Txt { text: modelData.decision; font.pixelSize: 12; color: Theme.textDim; Layout.preferredWidth: 90 }
                        Txt { text: modelData.withOutcome + "/" + modelData.observations; font.pixelSize: 12; color: Theme.textDim
                              Layout.preferredWidth: 60; horizontalAlignment: Text.AlignRight }
                        Txt { text: modelData.netPct === null ? "—" : Theme.signedPct(modelData.netPct, 3); font.pixelSize: 13
                              color: modelData.netPct === null ? Theme.textMuted : modelData.netPct > 0 ? Theme.lime : Theme.coral
                              Layout.preferredWidth: 80; horizontalAlignment: Text.AlignRight }
                        Txt { text: modelData.ci90 ? Theme.signedPct(modelData.ci90[0], 2) + " … " + Theme.signedPct(modelData.ci90[1], 2) : "—"
                              font.pixelSize: 12; color: Theme.textMuted; Layout.preferredWidth: 140; horizontalAlignment: Text.AlignRight }
                    }
                }
            }
            // multi-day strategies: shadow only (decided in their window, scored after the holding ends, no orders)
            ColumnLayout {
                readonly property var d: fwdCard.f.daily || ({})
                visible: d.strategies !== undefined
                Layout.fillWidth: true
                Layout.topMargin: 10
                spacing: 0
                Txt {
                    Layout.fillWidth: true; wrapMode: Text.WordWrap
                    text: "multi-day rules (shadow only — the app never holds these overnight). each decision is recorded in its own "
                          + "window from that day's closing prices; if the app wasn't running, it's counted as missed, never filled in later. "
                          + "per holding = % after modeled costs."
                    color: Theme.textDim; font.pixelSize: 12; Layout.bottomMargin: 6
                }
                RowLayout {
                    Layout.fillWidth: true; spacing: 8
                    SectionLabel { text: "rule"; font.pixelSize: 10; Layout.fillWidth: true }
                    SectionLabel { text: "recorded · missed"; font.pixelSize: 10; Layout.preferredWidth: 120; horizontalAlignment: Text.AlignRight }
                    SectionLabel { text: "scored"; font.pixelSize: 10; Layout.preferredWidth: 60; horizontalAlignment: Text.AlignRight }
                    SectionLabel { text: "per holding"; font.pixelSize: 10; Layout.preferredWidth: 90; horizontalAlignment: Text.AlignRight }
                    SectionLabel { text: "vs SPY"; font.pixelSize: 10; Layout.preferredWidth: 80; horizontalAlignment: Text.AlignRight }
                }
                Repeater {
                    model: parent.d.strategies || []
                    delegate: RowLayout {
                        Layout.fillWidth: true; spacing: 8
                        Layout.preferredHeight: 28
                        Txt { text: modelData.hypothesis; font.pixelSize: 13; Layout.fillWidth: true; elide: Text.ElideRight }
                        Txt { text: modelData.recorded + " · " + modelData.missed; font.pixelSize: 12; color: Theme.textDim
                              Layout.preferredWidth: 120; horizontalAlignment: Text.AlignRight }
                        Txt { text: modelData.scored; font.pixelSize: 12; color: Theme.textDim; Layout.preferredWidth: 60; horizontalAlignment: Text.AlignRight }
                        Txt { text: modelData.netPct === null ? "—" : Theme.signedPct(modelData.netPct, 2); font.pixelSize: 13
                              color: modelData.netPct === null ? Theme.textMuted : modelData.netPct > 0 ? Theme.lime : Theme.coral
                              Layout.preferredWidth: 90; horizontalAlignment: Text.AlignRight }
                        Txt { text: modelData.vsSpyPct === null ? "—" : Theme.signedPct(modelData.vsSpyPct, 2); font.pixelSize: 12
                              color: Theme.textMuted; Layout.preferredWidth: 80; horizontalAlignment: Text.AlignRight }
                    }
                }
            }
        }

        // ---------------- research evidence (historical, clearly separated from this account's own results)
        Card {
            id: evidence
            Layout.fillWidth: true
            readonly property var r: app.research || ({})
            visible: r.rows !== undefined
            title: "what the evidence says"
            icon: "shield"
            subtitle: "HISTORICAL RESEARCH — not this account. unseen holdout " + (r.holdout || "") + " · " + (r.source || "")
            headerRight: [ Pill { text: "historical · holdout"; kind: "info" } ]

            Txt {
                Layout.fillWidth: true
                text: evidence.r.conclusion || ""
                font.pixelSize: 15; font.weight: Font.DemiBold
                wrapMode: Text.WordWrap
            }
            Txt {
                Layout.fillWidth: true
                text: "a $20 round trip costs about " + Number(evidence.r.breakEvenPct || 0).toFixed(2) + "% (spread, slippage, fees). "
                      + "the strongest signal measured is worth about " + Number(evidence.r.bestSignalPct || 0).toFixed(2) + "% — a quarter of that. "
                      + "percentages below are per trade (P/L ÷ the $20 position); dollars are on the $100 account."
                color: Theme.textDim; font.pixelSize: 12; wrapMode: Text.WordWrap
            }
            // table
            EvidenceTable {
                Layout.fillWidth: true
                Layout.topMargin: 4
                rows: evidence.r.rows || []
            }
            // model + language model, side by side
            RowLayout {
                Layout.fillWidth: true
                Layout.topMargin: 8
                spacing: Theme.gap
                Rectangle {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Layout.preferredWidth: 1
                    implicitHeight: modelCol.implicitHeight + 28
                    radius: Theme.radiusSm
                    color: Theme.surface2
                    ColumnLayout {
                        id: modelCol
                        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 14
                        spacing: 4
                        RowLayout {
                            spacing: 8
                            Icon { name: "brain"; size: 15; color: Theme.textDim }
                            Txt { text: "statistical model"; font.pixelSize: 13; font.weight: Font.DemiBold }
                            Pill { text: (app.state.quant && app.state.quant.stage) ? app.state.quant.stage : "shadow"; kind: "neutral"; fontSize: 10 }
                        }
                        Txt {
                            Layout.fillWidth: true; wrapMode: Text.WordWrap
                            readonly property var m: evidence.r.model || ({})
                            text: "on " + (m.n || 0).toLocaleString(Qt.locale("en_US"), 'f', 0) + " unseen signals its win chances were calibrated (error "
                                  + Number(m.ece || 0).toFixed(3) + ") and better than the base rate (Brier " + Number(m.brier || 0).toFixed(3) + " vs "
                                  + Number(m.brierBase || 0).toFixed(3) + ", AUC " + Number(m.auc || 0).toFixed(2) + "). it can't predict the SIZE of results "
                                  + "(AUC " + Number(m.expNetAuc || 0).toFixed(2) + "), found nothing with a positive expected result and took no trades."
                            color: Theme.textDim; font.pixelSize: 12
                        }
                        Txt { text: "shadow = shows its estimate on every setup, never trades."; color: Theme.textMuted; font.pixelSize: 11 }
                    }
                }
                Rectangle {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Layout.preferredWidth: 1
                    implicitHeight: llmCol.implicitHeight + 28
                    radius: Theme.radiusSm
                    color: Theme.surface2
                    ColumnLayout {
                        id: llmCol
                        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 14
                        spacing: 4
                        RowLayout {
                            spacing: 8
                            Icon { name: "ai"; size: 15; color: Theme.textDim }
                            Txt { text: "language model (local 3B)"; font.pixelSize: 13; font.weight: Font.DemiBold }
                            Pill { text: "no trade authority"; kind: "warn"; fontSize: 10 }
                        }
                        Txt {
                            Layout.fillWidth: true; wrapMode: Text.WordWrap
                            readonly property var l: evidence.r.llm || ({})
                            text: "approved " + (l.approved || 0) + " of " + (l.answers || 0) + " picks at ~" + Math.round((l.avgConfidence || 0) * 100)
                                  + "% confidence — the same trades as the rules alone. " + Math.round((l.inconsistentShare || 0) * 100)
                                  + "% of its explanations contradicted the app's own numbers; median " + Math.round(l.medianSeconds || 0) + " s per answer."
                            color: Theme.textDim; font.pixelSize: 12
                        }
                        Txt { text: "it still explains and reviews; with real money it never decides."; color: Theme.textMuted; font.pixelSize: 11 }
                    }
                }
            }
        }

        // ---------------- research phase 2 (historical; same rules as above, a second untouched holdout)
        Card {
            id: evidence2
            Layout.fillWidth: true
            readonly property var r: app.research2 || ({})
            visible: r.rows !== undefined
            title: "phase 2: holding longer, stocks in play, cheaper entries"
            icon: "shield"
            subtitle: "HISTORICAL RESEARCH — not this account. unseen holdout " + (r.holdout || "") + " · " + (r.source || "")
            headerRight: [ Pill { text: "historical · holdout"; kind: "info" } ]

            Txt {
                Layout.fillWidth: true
                text: evidence2.r.conclusion || ""
                font.pixelSize: 15; font.weight: Font.DemiBold
                wrapMode: Text.WordWrap
            }
            Txt {
                Layout.fillWidth: true
                text: evidence2.r.note || ""
                color: Theme.textDim; font.pixelSize: 12; wrapMode: Text.WordWrap
            }
            EvidenceTable {
                Layout.fillWidth: true
                Layout.topMargin: 4
                rows: evidence2.r.rows || []
            }
            Txt {
                Layout.fillWidth: true
                visible: text.length > 0
                text: evidence2.r.trend || ""
                color: Theme.textMuted; font.pixelSize: 11; wrapMode: Text.WordWrap
            }
        }

        // ---------------- research phase 3 (historical; multi-day rules, simulation only)
        Card {
            id: evidence3
            Layout.fillWidth: true
            readonly property var r: app.research3 || ({})
            visible: r.rows !== undefined
            title: "phase 3: holding for days and weeks"
            icon: "shield"
            subtitle: "HISTORICAL RESEARCH — not this account. unseen holdout " + (r.holdout || "") + " · " + (r.source || "")
            headerRight: [ Pill { text: "historical · holdout"; kind: "info" } ]

            Txt {
                Layout.fillWidth: true
                text: evidence3.r.conclusion || ""
                font.pixelSize: 15; font.weight: Font.DemiBold
                wrapMode: Text.WordWrap
            }
            Txt {
                Layout.fillWidth: true
                text: evidence3.r.note || ""
                color: Theme.textDim; font.pixelSize: 12; wrapMode: Text.WordWrap
            }
            EvidenceTable {
                Layout.fillWidth: true
                Layout.topMargin: 4
                rows: evidence3.r.rows || []
                moneyLabel: evidence3.r.moneyLabel || "$ on $100"
            }
            Txt {
                Layout.fillWidth: true
                visible: text.length > 0
                text: evidence3.r.trend || ""
                color: Theme.textMuted; font.pixelSize: 11; wrapMode: Text.WordWrap
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
                Layout.preferredWidth: 3
                Layout.preferredHeight: 390
                Layout.alignment: Qt.AlignTop
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
                Layout.preferredWidth: 2
                Layout.preferredHeight: 390
                Layout.alignment: Qt.AlignTop
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
                Layout.preferredWidth: 3
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
                Layout.preferredWidth: 2
                Layout.alignment: Qt.AlignTop
                spacing: Theme.gap
                Card {
                    Layout.fillWidth: true
                    title: "decision approaches compared"
                    tip: "shadow trades"
                    subtitle: "every setup is followed with identical exit rules whether or not it was traded, so these compare like with like. after costs."
                    readonly property var ap: page.perf.approaches || ({ rows: [] })
                    Repeater {
                        model: (page.perf.approaches || {}).rows || []
                        delegate: RowLayout {
                            Layout.fillWidth: true
                            spacing: 10
                            Txt { text: modelData.label; font.pixelSize: 13; font.weight: Font.Medium; Layout.preferredWidth: 130; Layout.minimumWidth: 130; Layout.maximumWidth: 130 }
                            Txt { text: (modelData.count || 0) + " setups"; color: (modelData.count || 0) >= ((page.perf.approaches || {}).minSample || 30) ? Theme.textDim : Theme.textMuted; font.pixelSize: 12; Layout.preferredWidth: 74; Layout.minimumWidth: 74; Layout.maximumWidth: 74 }
                            Txt { text: (modelData.count || 0) > 0 ? Theme.signedPct(modelData.avgReturnPct, 3) : "—"; color: Theme.pnlColor(modelData.avgReturnPct); font.pixelSize: 13; Layout.fillWidth: true }
                            Txt { text: (modelData.count || 0) > 0 ? Theme.pct((modelData.winRate || 0) * 100, 0) + " win" : ""; color: Theme.textDim; font.pixelSize: 12 }
                        }
                    }
                    Rectangle {
                        Layout.fillWidth: true
                        implicitHeight: verdictTxt.implicitHeight + 16
                        radius: 10
                        readonly property string v: (page.perf.approaches || {}).verdict || "not_enough_data"
                        color: v === "ai_helps" ? Theme.limeSoft : v === "not_enough_data" ? Theme.surface2 : Theme.amberSoft
                        Txt {
                            id: verdictTxt
                            anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; anchors.margins: 10
                            text: (page.perf.approaches || {}).text || ""
                            color: parent.v === "ai_helps" ? Theme.lime : parent.v === "not_enough_data" ? Theme.textDim : Theme.amber
                            font.pixelSize: 12; wrapMode: Text.WordWrap; elide: Text.ElideNone
                        }
                    }
                    Txt {
                        Layout.fillWidth: true
                        text: "AI cost per decision: " + Theme.money((page.perf.approaches || {}).aiCostPerDecision || 0, 4)
                              + " · confidence check: " + ((page.perf.calibration || {}).text || "not measured yet")
                        color: Theme.textMuted; font.pixelSize: 11; wrapMode: Text.WordWrap; elide: Text.ElideNone
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
                    Rectangle { Layout.fillWidth: true; height: 1; color: Theme.border }
                    RowLayout {
                        Layout.fillWidth: true
                        Txt { text: "net result after running costs"; font.pixelSize: 13; font.weight: Font.Medium }
                        InfoTip { term: "realized" }
                        Item { Layout.fillWidth: true }
                        Txt { text: Theme.signedMoney((page.perf.costs || {}).netAfterOperating || 0); color: Theme.pnlColor((page.perf.costs || {}).netAfterOperating); font.pixelSize: 14; font.weight: Font.DemiBold }
                    }
                    Txt {
                        Layout.fillWidth: true
                        text: "trading profit/loss (fees already taken out) minus everything spent on cloud AI (" + Theme.money((page.perf.costs || {}).aiAllTime || 0, 3) + ") and market data (" + Theme.money((page.perf.costs || {}).dataSoFar || 0) + ") so far."
                        color: Theme.textMuted; font.pixelSize: 11; wrapMode: Text.WordWrap; elide: Text.ElideNone
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
                                : "replays each strategy on real 1-minute history with spread, slippage and fees: first half to train, next quarter to validate, last quarter held out as an unseen test."
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
                    SectionLabel { text: "strategy"; font.pixelSize: 10; Layout.preferredWidth: 196; Layout.minimumWidth: 196; Layout.maximumWidth: 196 }
                    SectionLabel { text: "trades"; font.pixelSize: 10; Layout.preferredWidth: 60; Layout.minimumWidth: 60; Layout.maximumWidth: 60 }
                    SectionLabel { text: "train"; font.pixelSize: 10; Layout.preferredWidth: 82; Layout.minimumWidth: 82; Layout.maximumWidth: 82 }
                    SectionLabel { text: "validate"; font.pixelSize: 10; Layout.preferredWidth: 82; Layout.minimumWidth: 82; Layout.maximumWidth: 82 }
                    SectionLabel { text: "test"; font.pixelSize: 10; Layout.preferredWidth: 82; Layout.minimumWidth: 82; Layout.maximumWidth: 82 }
                    SectionLabel { text: "random"; font.pixelSize: 10; Layout.preferredWidth: 78; Layout.minimumWidth: 78; Layout.maximumWidth: 78 }
                    SectionLabel { text: "2× slip"; font.pixelSize: 10; Layout.preferredWidth: 82; Layout.minimumWidth: 82; Layout.maximumWidth: 82 }
                    SectionLabel { text: "stable"; font.pixelSize: 10; Layout.preferredWidth: 56; Layout.minimumWidth: 56; Layout.maximumWidth: 56 }
                    SectionLabel { text: "adj. p"; font.pixelSize: 10; Layout.preferredWidth: 52; Layout.minimumWidth: 52; Layout.maximumWidth: 52 }
                    SectionLabel { text: "verdict"; font.pixelSize: 10; Layout.fillWidth: true }
                }
                Repeater {
                    model: page.btr ? Object.keys(page.btr.strategies) : []
                    delegate: ColumnLayout {
                        readonly property var r: page.btr.strategies[modelData]
                        readonly property var tr: r.train || r.inSample || ({})
                        readonly property var va: r.validation || ({})
                        readonly property var te: r.test || r.outOfSample || ({})
                        readonly property var folds: r.walkForward || []
                        readonly property int usedFolds: folds.filter(function(f) { return f.trades > 0 }).length
                        readonly property int goodFolds: folds.filter(function(f) { return f.trades > 0 && f.avgRetPct > 0 }).length
                        readonly property var ra: r.riskAdjusted || ({})
                        Layout.fillWidth: true
                        spacing: 3
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 0
                            Txt { text: r.name; font.pixelSize: 14; font.weight: Font.Medium; Layout.preferredWidth: 196; Layout.minimumWidth: 196; Layout.maximumWidth: 196 }
                            Txt { text: te.trades === undefined ? "—" : te.trades; font.pixelSize: 13; Layout.preferredWidth: 60; Layout.minimumWidth: 60; Layout.maximumWidth: 60 }
                            Txt { text: tr.trades ? Theme.signedPct(tr.avgRetPct, 3) : "—"; color: Theme.pnlColor(tr.avgRetPct); font.pixelSize: 13; Layout.preferredWidth: 82; Layout.minimumWidth: 82; Layout.maximumWidth: 82 }
                            Txt { text: va.trades ? Theme.signedPct(va.avgRetPct, 3) : "—"; color: Theme.pnlColor(va.avgRetPct); font.pixelSize: 13; Layout.preferredWidth: 82; Layout.minimumWidth: 82; Layout.maximumWidth: 82 }
                            Txt { text: te.trades ? Theme.signedPct(te.avgRetPct, 3) : "—"; color: Theme.pnlColor(te.avgRetPct); font.pixelSize: 13; font.weight: Font.DemiBold; Layout.preferredWidth: 82; Layout.minimumWidth: 82; Layout.maximumWidth: 82 }
                            Txt { text: r.randomBaseline && r.randomBaseline.trades ? Theme.signedPct(r.randomBaseline.avgRetPct, 3) : "—"; color: Theme.textDim; font.pixelSize: 13; Layout.preferredWidth: 78; Layout.minimumWidth: 78; Layout.maximumWidth: 78 }
                            Txt { text: r.stressAvgRetPct === undefined || !te.trades ? "—" : Theme.signedPct(r.stressAvgRetPct, 3); color: Theme.pnlColor(r.stressAvgRetPct); font.pixelSize: 13; Layout.preferredWidth: 82; Layout.minimumWidth: 82; Layout.maximumWidth: 82 }
                            Txt { text: usedFolds > 0 ? goodFolds + "/" + usedFolds : "—"; color: usedFolds > 0 && goodFolds / usedFolds >= 0.6 ? Theme.text : Theme.textDim; font.pixelSize: 13; Layout.preferredWidth: 56; Layout.minimumWidth: 56; Layout.maximumWidth: 56 }
                            Txt { text: r.pAdjusted === undefined ? Theme.fixed(te.pValue || 1, 2) : Theme.fixed(r.pAdjusted, 2); font.pixelSize: 13; Layout.preferredWidth: 52; Layout.minimumWidth: 52; Layout.maximumWidth: 52 }
                            Pill {
                                text: r.verdict === "evidence" ? (page.btr.source.indexOf("synthetic") >= 0 ? "SYNTHETIC ONLY" : "EVIDENCE ✓") : r.verdict === "synthetic_only" ? "SYNTHETIC ONLY" : r.verdict === "no_edge" ? "NO EDGE" : "TOO FEW TRADES"
                                kind: r.verdict === "evidence" && page.btr.source.indexOf("synthetic") < 0 ? "lime" : r.verdict === "no_edge" ? "sell" : "warn"
                            }
                            Item { Layout.fillWidth: true }
                        }
                        Txt { text: r.explanation; color: Theme.textMuted; font.pixelSize: 12; wrapMode: Text.WordWrap; elide: Text.ElideNone; Layout.fillWidth: true }
                        Txt {
                            visible: te.trades > 0
                            Layout.fillWidth: true; Layout.bottomMargin: 6
                            text: "test period on $100: " + Theme.signedPct(ra.totalReturnPct || 0) + " · worst drop " + Theme.pct(ra.maxDrawdownPct || 0) + " · sharpe " + Theme.fixed(ra.sharpe || 0, 2)
                                  + (r.widenedShare > 0 ? " · stops widened for costs on " + Math.round(r.widenedShare * 100) + "% of trades" : "")
                                  + (r.regimes ? " · " + Object.keys(r.regimes).filter(function(k) { return r.regimes[k].trades > 0 }).map(function(k) { return k + ": " + Theme.signedPct(r.regimes[k].avgRetPct, 2) }).join(" · ") : "")
                            color: Theme.textMuted; font.pixelSize: 11; wrapMode: Text.WordWrap; elide: Text.ElideNone
                        }
                    }
                }

                // ---- how would the whole app have done, and against what
                SectionLabel { visible: page.btr && page.btr.approaches !== undefined; text: "the whole app on the unseen test period"; Layout.topMargin: 6 }
                Repeater {
                    model: page.btr && page.btr.approaches ? ["every_signal", "first_come", "ranked", "random"] : []
                    delegate: RowLayout {
                        readonly property var a: page.btr.approaches[modelData] || ({})
                        Layout.fillWidth: true
                        spacing: 0
                        Rectangle { width: 8; height: 8; radius: 4; color: modelData === "ranked" ? Theme.lime : Theme.textMuted; Layout.rightMargin: 10 }
                        Txt { text: a.label || modelData; font.pixelSize: 13; font.weight: modelData === "ranked" ? Font.DemiBold : Font.Normal; Layout.preferredWidth: 300; Layout.minimumWidth: 300; Layout.maximumWidth: 300 }
                        Txt { text: (a.trades || 0) + " trades"; color: Theme.textDim; font.pixelSize: 13; Layout.preferredWidth: 90; Layout.minimumWidth: 90; Layout.maximumWidth: 90 }
                        Txt { text: a.trades ? Theme.signedPct(a.avgRetPct, 3) + " / trade" : "—"; color: Theme.pnlColor(a.avgRetPct); font.pixelSize: 13; Layout.preferredWidth: 130; Layout.minimumWidth: 130; Layout.maximumWidth: 130 }
                        Txt { text: a.trades ? Theme.signedMoney(a.totalPnl) : ""; color: Theme.pnlColor(a.totalPnl); font.pixelSize: 13; Layout.preferredWidth: 90; Layout.minimumWidth: 90; Layout.maximumWidth: 90 }
                        Txt { text: a.trades ? "worst drop " + Theme.money(a.maxDrawdown) : ""; color: Theme.textMuted; font.pixelSize: 12; Layout.fillWidth: true }
                    }
                }
                Txt {
                    visible: page.btr && page.btr.benchmarks !== undefined
                    Layout.fillWidth: true
                    readonly property var bm: (page.btr && page.btr.benchmarks) || ({})
                    text: "benchmarks over the same days: buying SPY and holding " + (bm.spyBuyHoldPct === null || bm.spyBuyHoldPct === undefined ? "—" : Theme.signedPct(bm.spyBuyHoldPct))
                          + " · SPY open→close each day " + (bm.spyOpenToCloseAvgPct === null || bm.spyOpenToCloseAvgPct === undefined ? "—" : Theme.signedPct(bm.spyOpenToCloseAvgPct, 3) + " avg")
                          + " · doing nothing $0.00. the app only earns its keep if it beats these after costs."
                    color: Theme.textDim; font.pixelSize: 12; wrapMode: Text.WordWrap; elide: Text.ElideNone
                }
                Txt {
                    visible: page.btr && page.btr.coverage !== undefined
                    Layout.fillWidth: true
                    readonly property var cv: (page.btr && page.btr.coverage) || ({})
                    text: "data: " + (cv.symbolDaysWithData || 0) + " of " + (cv.symbolDaysExpected || 0) + " stock-days available"
                          + ((cv.missing || []).length > 0 ? " — missing ones (e.g. " + cv.missing.slice(0, 3).join(", ") + ") were skipped, never filled in." : " — nothing missing.")
                          + (page.btr && page.btr.splits && page.btr.splits.train ? " train " + page.btr.splits.train.join("→") + " · validation " + (page.btr.splits.validation || []).join("→") + " · test " + (page.btr.splits.test || []).join("→") : "")
                    color: Theme.textMuted; font.pixelSize: 11; wrapMode: Text.WordWrap; elide: Text.ElideNone
                }
                Txt {
                    Layout.fillWidth: true
                    text: (page.btr && page.btr.aiNote ? page.btr.aiNote + " " : "") + "all results are after spread, slippage and fees. \"random\" uses the same stops, targets and holding times with random entry times — a strategy has to beat that to matter. \"stable\" = profitable walk-forward periods. \"adj. p\" accounts for testing several strategies at once. past results don't guarantee future ones."
                    color: Theme.textMuted; font.pixelSize: 12; wrapMode: Text.WordWrap; elide: Text.ElideNone
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
