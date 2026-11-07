import QtQuick
import QtQuick.Layouts
import QtQuick.Controls.Basic
import Sp 1.0
import SpCharts 1.0

Item {
    id: page
    readonly property var st: app.state
    readonly property var ai: st.ai || ({})
    readonly property var regime: st.regime || ({})
    readonly property var decision: st.lastDecision || null
    // the last time setups were actually compared (stays visible while later scans find nothing)
    readonly property var comparison: st.lastComparison || null
    readonly property var calib: (app.performance && app.performance.calibration) || ({})
    property string filter: "all"

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 28
        anchors.topMargin: 4
        spacing: Theme.gap

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.gap

            // AI status
            Rectangle {
                Layout.fillWidth: true
                Layout.preferredWidth: 3
                implicitHeight: 150
                radius: Theme.radius
                color: Theme.surface
                border.color: Theme.border
                RowLayout {
                    anchors.fill: parent
                    anchors.margins: 20
                    spacing: 20
                    Rectangle {
                        width: 64; height: 64; radius: 20
                        color: Theme.surface2
                        Icon { anchors.centerIn: parent; name: "ai"; size: 30; color: page.ai.healthy === false && page.ai.decisionMode !== "rules" ? Theme.coral : Theme.lime }
                        Rectangle {
                            visible: page.ai.busy === true || (page.ai.pending || []).length > 0
                            anchors.fill: parent; anchors.margins: -5; radius: 25
                            color: "transparent"; border.width: 2; border.color: Theme.lime
                            SequentialAnimation on opacity { loops: Animation.Infinite; running: parent.visible
                                NumberAnimation { from: 0.2; to: 1; duration: 700 } NumberAnimation { from: 1; to: 0.2; duration: 700 } }
                        }
                    }
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 4
                        RowLayout {
                            spacing: 8
                            Txt { text: page.ai.decisionMode === "rules" ? "rules-only mode" : (page.ai.provider === "anthropic" ? "cloud ai · " : "local ai · ") + (page.ai.model || ""); font.family: Theme.display; font.pixelSize: 19; font.weight: Font.Medium }
                            Pill { text: page.ai.decisionMode === "rules" ? "AI NOT USED" : page.ai.healthy === true ? "ONLINE" : page.ai.healthy === false ? "OFFLINE" : "CHECKING"; kind: page.ai.decisionMode === "rules" ? "neutral" : page.ai.healthy === true ? "lime" : page.ai.healthy === false ? "sell" : "warn" }
                        }
                        Txt {
                            Layout.fillWidth: true
                            text: page.ai.decisionMode === "rules" ? "strategies find setups and the ranker picks the best after costs; the risk engine still checks every trade."
                                : (page.ai.pending || []).length > 0 ? "reviewing " + page.ai.pending.join(", ") + " right now…"
                                : (page.ai.message || "")
                            color: Theme.textDim; font.pixelSize: 13; wrapMode: Text.WordWrap; elide: Text.ElideNone
                        }
                        Txt {
                            visible: page.ai.canCall === false && page.ai.decisionMode !== "rules"
                            text: "not consulting the AI: " + (page.ai.why || "")
                            color: Theme.amber; font.pixelSize: 12; wrapMode: Text.WordWrap; Layout.fillWidth: true
                        }
                    }
                    ColumnLayout {
                        spacing: 2
                        visible: page.ai.isPaid === true
                        Ring {
                            Layout.alignment: Qt.AlignHCenter
                            width: 56; height: 56; thickness: 6
                            value: (page.ai.budget || 0) > 0 ? (page.ai.monthCost || 0) / page.ai.budget : 0
                            color: value > 0.8 ? Theme.coral : Theme.lime
                            trackColor: Theme.surface3
                        }
                        Txt { text: Theme.money(page.ai.monthCost || 0, 3) + " / " + Theme.money(page.ai.budget || 0); font.pixelSize: 12; color: Theme.textDim; Layout.alignment: Qt.AlignHCenter }
                        Txt { text: "cloud spend this month"; font.pixelSize: 11; color: Theme.textMuted; Layout.alignment: Qt.AlignHCenter }
                    }
                    ColumnLayout {
                        spacing: 2
                        visible: page.ai.isPaid !== true && page.ai.decisionMode !== "rules"
                        Txt { text: "$0.00"; font.family: Theme.display; font.pixelSize: 22; color: Theme.lime; Layout.alignment: Qt.AlignHCenter }
                        Txt { text: "runs on your computer"; font.pixelSize: 11; color: Theme.textMuted; Layout.alignment: Qt.AlignHCenter }
                    }
                }
            }

            // market conditions + how it decides
            Rectangle {
                Layout.fillWidth: true
                Layout.preferredWidth: 2
                implicitHeight: 150
                radius: Theme.radius
                color: Theme.surface
                border.color: Theme.border
                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 20
                    spacing: 8
                    RowLayout {
                        spacing: 8
                        SectionLabel { text: "market right now" }
                        Pill {
                            text: (page.regime.label || "unknown").toUpperCase()
                            kind: page.regime.label === "trending up" ? "lime" : page.regime.label === "risk-off" ? "sell"
                                : page.regime.label === "volatile" ? "warn" : "neutral"
                        }
                        Item { Layout.fillWidth: true }
                    }
                    Txt { Layout.fillWidth: true; text: page.regime.text || "waiting for market data"; color: Theme.textDim; font.pixelSize: 12; wrapMode: Text.WordWrap; elide: Text.ElideNone; maximumLineCount: 2 }
                    RowLayout {
                        spacing: 14
                        Repeater {
                            model: [
                                { k: "S&P 500", v: page.regime.spyChangePct === undefined || page.regime.spyChangePct === null ? "—" : Theme.pct(page.regime.spyChangePct) },
                                { k: "stocks rising", v: page.regime.breadthPct === undefined || page.regime.breadthPct === null ? "—" : Math.round(page.regime.breadthPct) + "%" },
                                { k: "swing vs normal", v: page.regime.volRatio === undefined || page.regime.volRatio === null ? "—" : page.regime.volRatio.toFixed(1) + "×" }
                            ]
                            delegate: ColumnLayout {
                                spacing: 0
                                Txt { text: modelData.v; font.family: Theme.display; font.pixelSize: 16; font.weight: Font.Medium }
                                Txt { text: modelData.k; color: Theme.textMuted; font.pixelSize: 11 }
                            }
                        }
                    }
                }
            }
        }

        // the latest comparison: every setup it looked at, which it picked, and why the rest lost
        Rectangle {
            Layout.fillWidth: true
            implicitHeight: cmpCol.implicitHeight + 36
            radius: Theme.radius
            color: Theme.surface
            border.color: Theme.border
            ColumnLayout {
                id: cmpCol
                anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                anchors.margins: 18
                spacing: 10
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 10
                    Txt { text: "latest decision"; font.family: Theme.display; font.pixelSize: 15; font.weight: Font.Medium }
                    Repeater {
                        model: [
                            { n: "1", t: "scan " + ((st.scanner && st.scanner.universeSize) || "hundreds of") + " stocks" },
                            { n: "2", t: "rank & compare after costs" },
                            { n: "3", t: page.ai.decisionMode === "rules" ? "no AI (rules + ranker)" : page.ai.decisionMode === "ai_select" ? "AI picks one or none" : "AI confirms each pick" },
                            { n: "4", t: "risk engine has the final say" }
                        ]
                        delegate: RowLayout {
                            spacing: 5
                            Rectangle { width: 18; height: 18; radius: 9; color: Theme.limeSoft
                                Txt { anchors.centerIn: parent; text: modelData.n; color: Theme.lime; font.pixelSize: 10; font.weight: Font.Bold } }
                            Txt { text: modelData.t; color: Theme.textDim; font.pixelSize: 12 }
                        }
                    }
                    Item { Layout.fillWidth: true }
                }
                Txt {
                    Layout.fillWidth: true
                    text: page.decision ? page.decision.summary : (st.session && st.session.isOpen === false ? "not looking for new trades right now — " + (st.session.text || "market closed") : "no setups compared yet. the scanner needs a few minutes of fresh data after the open.")
                    color: page.decision && page.decision.chosen.length > 0 ? Theme.lime : Theme.text
                    font.pixelSize: 13; wrapMode: Text.WordWrap; elide: Text.ElideNone
                }
                Txt {
                    Layout.fillWidth: true
                    visible: page.comparison !== null && (page.decision === null || page.decision.candidates.length === 0)
                    text: page.comparison ? "last comparison at " + page.comparison.at + ": " + page.comparison.summary : ""
                    color: Theme.textMuted; font.pixelSize: 12; wrapMode: Text.WordWrap
                }
                Flow {
                    Layout.fillWidth: true
                    spacing: 10
                    readonly property var shown: page.decision && page.decision.candidates.length > 0 ? page.decision : page.comparison
                    visible: shown !== null && shown !== undefined && shown.candidates.length > 0
                    Repeater {
                        model: parent.shown ? parent.shown.candidates.slice(0, 6) : []
                        delegate: Rectangle {
                            width: 228
                            height: candCol.implicitHeight + 20
                            radius: 12
                            color: Theme.surface2
                            border.color: modelData.take ? Theme.lime : "transparent"
                            border.width: modelData.take ? 1.5 : 0
                            ColumnLayout {
                                id: candCol
                                anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                                anchors.margins: 10
                                spacing: 4
                                RowLayout {
                                    Layout.fillWidth: true
                                    Txt { text: modelData.symbol; font.family: Theme.display; font.pixelSize: 15; font.weight: Font.Bold }
                                    Pill { text: modelData.take ? "PICKED" : "PASSED"; kind: modelData.take ? "lime" : "neutral"; fontSize: 10 }
                                    Item { Layout.fillWidth: true }
                                    Txt { text: Math.round(modelData.score * 100); font.pixelSize: 13; font.weight: Font.DemiBold; color: modelData.take ? Theme.lime : Theme.textDim }
                                }
                                Txt { text: modelData.strategy + (modelData.agreeing > 1 ? " · " + modelData.agreeing + " agree" : ""); color: Theme.textMuted; font.pixelSize: 11 }
                                Txt { text: "+" + modelData.targetPct.toFixed(2) + "% / −" + modelData.stopPct.toFixed(2) + "% · costs " + modelData.costPct.toFixed(2) + "% · " + modelData.rrAfterCost.toFixed(1) + "× after costs"; color: Theme.textDim; font.pixelSize: 11; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                                RowLayout {
                                    visible: modelData.quant !== null && modelData.quant !== undefined
                                    Layout.fillWidth: true
                                    spacing: 6
                                    Icon { name: "brain"; size: 12; color: Theme.textMuted }
                                    Txt {
                                        Layout.fillWidth: true
                                        readonly property var q: modelData.quant || ({})
                                        text: "model: " + (q.expNetPct >= 0 ? "+" : "") + Number(q.expNetPct || 0).toFixed(2) + "%"
                                              + (q.expNetSd !== null && q.expNetSd !== undefined ? " ± " + Number(q.expNetSd).toFixed(2) : "")
                                              + " expected · win " + Math.round((q.pWin || 0) * 100) + "%"
                                              + (q.blocks ? " · blocked" : " · shadow")
                                        color: (q.expNetPct || 0) > 0 ? Theme.lime : Theme.textMuted
                                        font.pixelSize: 11
                                        wrapMode: Text.WordWrap
                                        elide: Text.ElideNone
                                        maximumLineCount: 2
                                    }
                                    HoverHandler { id: qh }
                                    ToolTip.visible: qh.hovered
                                    ToolTip.delay: 300
                                    ToolTip.text: "the statistical model's estimate after costs, as % of this position (not of the account). "
                                                  + "± is the spread of its estimate across resampled training days. in shadow it only records; it can never add a trade."
                                }
                                Txt { visible: !modelData.take && modelData.blockers.length > 0; text: modelData.blockers.length > 0 ? modelData.blockers[0] : ""; color: Theme.amber; font.pixelSize: 11; Layout.fillWidth: true; wrapMode: Text.WordWrap; elide: Text.ElideNone; maximumLineCount: 3 }
                            }
                        }
                    }
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: Theme.gap

            // decisions feed
            Rectangle {
                Layout.fillWidth: true
                Layout.preferredWidth: 3
                Layout.fillHeight: true
                radius: Theme.radius
                color: Theme.surface
                border.color: Theme.border
                clip: true
                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 20
                    spacing: 12
                    RowLayout {
                        Txt { text: "ai decisions"; font.family: Theme.display; font.pixelSize: 15; font.weight: Font.Medium }
                        InfoTip { term: "confidence" }
                        Item { Layout.fillWidth: true }
                        Txt { text: app.decisionsModel.count + " recent"; color: Theme.textMuted; font.pixelSize: 12 }
                    }
                    Txt {
                        // confidence numbers are shown only next to what they've actually been worth
                        Layout.fillWidth: true
                        visible: app.decisionsModel.count > 0
                        text: "does confidence mean anything? " + (page.calib.text || "not measured yet.")
                        color: page.calib.verdict === "informative" ? Theme.textDim : Theme.amber
                        font.pixelSize: 11; wrapMode: Text.WordWrap; elide: Text.ElideNone
                    }
                    ListView {
                        id: dlist
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        spacing: 10
                        model: app.decisionsModel
                        boundsBehavior: Flickable.StopAtBounds
                        ScrollBar.vertical: ScrollBar { contentItem: Rectangle { implicitWidth: 6; radius: 3; color: Theme.borderStrong } }
                        add: Transition { NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 300 } }
                        displaced: Transition { NumberAnimation { properties: "y"; duration: 300; easing.type: Easing.OutCubic } }
                        delegate: Rectangle {
                            width: dlist.width - 8
                            height: dcol.implicitHeight + 28
                            radius: 14
                            color: Theme.surface2
                            RowLayout {
                                anchors.fill: parent
                                anchors.margins: 14
                                spacing: 14
                                Item {
                                    Layout.alignment: Qt.AlignTop
                                    width: 50; height: 50
                                    Ring {
                                        anchors.fill: parent
                                        thickness: 4.5
                                        value: model.confidence >= 0 ? model.confidence : 0
                                        color: model.action === "buy" ? Theme.lime : model.action === "sell" ? Theme.coral : Theme.slate
                                        trackColor: Theme.surface3
                                    }
                                    Txt { anchors.centerIn: parent; text: model.confidence >= 0 ? Math.round(model.confidence * 100) + "%" : "—"; font.pixelSize: 12; font.weight: Font.DemiBold }
                                }
                                ColumnLayout {
                                    id: dcol
                                    Layout.fillWidth: true
                                    spacing: 5
                                    RowLayout {
                                        spacing: 8
                                        Txt { text: model.kind === "select" && model.symbol === "none" ? "no trade" : model.symbol; font.family: Theme.display; font.pixelSize: 16; font.weight: Font.Bold }
                                        ActionChip { action: model.valid ? model.action : "error" }
                                        Pill { visible: model.kind === "exit_review"; text: "position check"; kind: "info" }
                                        Pill { visible: model.kind === "select"; text: "compared options"; kind: "info" }
                                        Pill { visible: model.evidenceQuality !== ""; text: "evidence: " + model.evidenceQuality; kind: model.evidenceQuality === "strong" ? "lime" : model.evidenceQuality === "moderate" ? "neutral" : "warn" }
                                        Item { Layout.fillWidth: true }
                                        Txt { text: model.timeText; color: Theme.textMuted; font.pixelSize: 11 }
                                    }
                                    Txt { visible: model.strategy !== ""; text: "setup: " + model.strategy; color: Theme.textMuted; font.pixelSize: 12 }
                                    Txt { text: model.reasoning; color: model.valid ? Theme.text : Theme.amber; font.pixelSize: 13; wrapMode: Text.WordWrap; elide: Text.ElideNone; Layout.fillWidth: true; lineHeight: 1.25 }
                                    Txt { visible: model.risks !== ""; text: "risks: " + model.risks; color: Theme.textDim; font.pixelSize: 12; wrapMode: Text.WordWrap; elide: Text.ElideNone; Layout.fillWidth: true }
                                    Txt { text: model.modelName + " · " + (model.latencyMs / 1000).toFixed(1) + "s" + (model.costUsd > 0 ? " · " + Theme.money(model.costUsd, 4) : " · free"); color: Theme.textMuted; font.pixelSize: 11 }
                                }
                            }
                        }
                    }
                }
                EmptyState {
                    anchors.centerIn: parent
                    visible: app.decisionsModel.count === 0
                    icon: "ai"
                    title: "the ai hasn't said anything yet"
                    message: st.ai && st.ai.decisionMode === "rules" ? "you're in rules-only mode, so the AI isn't consulted." : "it only speaks up when a strategy finds a setup worth a second look."
                }
            }

            // event log
            Rectangle {
                Layout.fillWidth: true
                Layout.preferredWidth: 2
                Layout.fillHeight: true
                radius: Theme.radius
                color: Theme.surface
                border.color: Theme.border
                clip: true
                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 20
                    spacing: 12
                    RowLayout {
                        Txt { text: "everything else"; font.family: Theme.display; font.pixelSize: 15; font.weight: Font.Medium }
                        Item { Layout.fillWidth: true }
                    }
                    Segmented {
                        small: true
                        options: [{ value: "all", label: "all" }, { value: "trade", label: "trades" }, { value: "decision", label: "decisions" }, { value: "risk", label: "safety" }, { value: "system", label: "system" }]
                        current: page.filter
                        onPicked: (v) => page.filter = v
                    }
                    ListView {
                        id: elist
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        spacing: 2
                        model: app.activityModel
                        boundsBehavior: Flickable.StopAtBounds
                        ScrollBar.vertical: ScrollBar { contentItem: Rectangle { implicitWidth: 6; radius: 3; color: Theme.borderStrong } }
                        delegate: Item {
                            readonly property bool shown: page.filter === "all" || model.category === page.filter || (page.filter === "decision" && model.category === "ai")
                            width: elist.width - 8
                            height: shown ? ecol.implicitHeight + 16 : 0
                            visible: shown
                            RowLayout {
                                anchors.fill: parent
                                anchors.topMargin: 8
                                spacing: 10
                                Rectangle {
                                    width: 8; height: 8; radius: 4
                                    Layout.alignment: Qt.AlignTop; Layout.topMargin: 5
                                    color: model.level === "error" ? Theme.coral : model.level === "warning" ? Theme.amber : model.level === "success" ? Theme.lime : Theme.textMuted
                                }
                                ColumnLayout {
                                    id: ecol
                                    Layout.fillWidth: true
                                    spacing: 2
                                    Txt { text: model.title; font.pixelSize: 13; font.weight: Font.Medium; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                                    Txt { text: model.message; visible: model.message !== ""; color: Theme.textDim; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.WordWrap; elide: Text.ElideNone }
                                    Txt { text: model.timeText + " · " + model.category; color: Theme.textMuted; font.pixelSize: 11 }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
