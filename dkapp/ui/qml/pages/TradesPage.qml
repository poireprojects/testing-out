import QtQuick
import QtQuick.Layouts
import QtQuick.Controls.Basic
import Dk 1.0

Item {
    id: page
    readonly property var st: app.state
    readonly property var perf: app.performance || ({})
    property string tab: "open"
    property int expanded: -1

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 28
        anchors.topMargin: 4
        spacing: Theme.gap

        RowLayout {
            Layout.fillWidth: true
            spacing: 12
            Segmented {
                options: [{ value: "open", label: "open positions" }, { value: "done", label: "completed trades" }, { value: "orders", label: "orders" }]
                current: page.tab
                onPicked: (v) => page.tab = v
            }
            Item { Layout.fillWidth: true }
            Repeater {
                model: [
                    { l: "trades", v: String((perf.stats || {}).count || 0) },
                    { l: "win rate", v: Theme.pct(((perf.stats || {}).winRate || 0) * 100, 0) },
                    { l: "net result", v: Theme.signedMoney((perf.stats || {}).total || 0), c: (perf.stats || {}).total || 0 },
                    { l: "fees paid", v: Theme.money(perf.feesTotal || 0) }
                ]
                delegate: ColumnLayout {
                    spacing: 0
                    Layout.leftMargin: 14
                    SectionLabel { text: modelData.l; font.pixelSize: 10; Layout.alignment: Qt.AlignRight }
                    Txt { text: modelData.v; font.family: Theme.display; font.pixelSize: 17; color: modelData.c !== undefined ? Theme.pnlColor(modelData.c) : Theme.text; Layout.alignment: Qt.AlignRight }
                }
            }
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            radius: Theme.radius
            color: Theme.surface
            border.color: Theme.border
            clip: true

            // ---------------- open positions
            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 20
                visible: page.tab === "open"
                spacing: 12
                ListView {
                    id: plist
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    spacing: 12
                    clip: true
                    model: app.positionsModel
                    delegate: Rectangle {
                        width: plist.width
                        height: pcol.implicitHeight + 32
                        radius: 16
                        color: Theme.surface2
                        ColumnLayout {
                            id: pcol
                            anchors.fill: parent
                            anchors.margins: 16
                            spacing: 10
                            RowLayout {
                                spacing: 10
                                Txt { text: model.symbol; font.family: Theme.display; font.pixelSize: 22; font.weight: Font.Bold }
                                ActionChip { action: model.status === "closing" ? "sell" : model.status === "pending_entry" ? "buy" : "hold" }
                                Pill { text: model.strategy; kind: "info" }
                                Pill { text: model.protection; kind: model.protection === "broker stop order" ? "neutral" : "warn" }
                                Item { Layout.fillWidth: true }
                                ColumnLayout {
                                    spacing: 0
                                    Txt { text: Theme.signedMoney(model.pnl); font.family: Theme.display; font.pixelSize: 20; color: Theme.pnlColor(model.pnl); Layout.alignment: Qt.AlignRight }
                                    Txt { text: Theme.signedPct(model.pnlPct); color: Theme.pnlColor(model.pnl); font.pixelSize: 12; Layout.alignment: Qt.AlignRight }
                                }
                            }
                            GridLayout {
                                columns: 6
                                columnSpacing: 26
                                rowSpacing: 2
                                Repeater {
                                    model: [
                                        { k: "shares", v: Theme.fixed(qty, 6) }, { k: "bought at", v: Theme.money(entryPrice) },
                                        { k: "now", v: Theme.money(price) }, { k: "value", v: Theme.money(value) },
                                        { k: "stop-loss", v: Theme.money(stop) }, { k: "target", v: Theme.money(target) }
                                    ]
                                    delegate: ColumnLayout {
                                        spacing: 1
                                        SectionLabel { text: modelData.k; font.pixelSize: 10 }
                                        Txt { text: modelData.v; font.pixelSize: 14 }
                                    }
                                }
                            }
                            Txt { text: "why: " + model.reasoning; color: Theme.textDim; font.pixelSize: 13; wrapMode: Text.WordWrap; elide: Text.ElideNone; Layout.fillWidth: true; visible: model.reasoning !== "" }
                            Txt { text: "selling because: " + model.exitReason; color: Theme.amber; font.pixelSize: 13; visible: model.status === "closing" && model.exitReason !== ""; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                            RowLayout {
                                Txt { text: "held " + model.heldMinutes + " min"; color: Theme.textMuted; font.pixelSize: 12 }
                                Item { Layout.fillWidth: true }
                                DkButton { compact: true; kind: "danger"; text: "sell now"; iconName: "down"; visible: model.status === "open"; onClicked: app.closePosition(model.tradeId) }
                            }
                        }
                    }
                }
                EmptyState {
                    visible: app.positionsModel.count === 0
                    Layout.alignment: Qt.AlignHCenter
                    Layout.topMargin: -parent.height / 2
                    icon: "eye"
                    title: "nothing open"
                    message: "all day trades are closed before the market closes, so evenings are always empty here."
                }
            }

            // ---------------- completed trades
            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 20
                visible: page.tab === "done"
                spacing: 8
                RowLayout {
                    Layout.fillWidth: true
                    Layout.leftMargin: 12
                    Layout.rightMargin: 12
                    spacing: 0
                    SectionLabel { text: "when"; font.pixelSize: 10; Layout.preferredWidth: 120; Layout.minimumWidth: 120; Layout.maximumWidth: 120 }
                    SectionLabel { text: "stock"; font.pixelSize: 10; Layout.preferredWidth: 80; Layout.minimumWidth: 80; Layout.maximumWidth: 80 }
                    SectionLabel { text: "strategy"; font.pixelSize: 10; Layout.preferredWidth: 170; Layout.minimumWidth: 170; Layout.maximumWidth: 170 }
                    SectionLabel { text: "entry → exit"; font.pixelSize: 10; Layout.preferredWidth: 170; Layout.minimumWidth: 170; Layout.maximumWidth: 170 }
                    SectionLabel { text: "held"; font.pixelSize: 10; Layout.preferredWidth: 60; Layout.minimumWidth: 60; Layout.maximumWidth: 60 }
                    SectionLabel { text: "why it ended"; font.pixelSize: 10; Layout.fillWidth: true }
                    SectionLabel { text: "result"; font.pixelSize: 10; Layout.preferredWidth: 110; Layout.minimumWidth: 110; Layout.maximumWidth: 110; horizontalAlignment: Text.AlignRight }
                }
                ListView {
                    id: tlist
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    spacing: 4
                    model: app.tradesModel
                    boundsBehavior: Flickable.StopAtBounds
                    ScrollBar.vertical: ScrollBar { contentItem: Rectangle { implicitWidth: 6; radius: 3; color: Theme.borderStrong } }
                    delegate: Rectangle {
                        width: tlist.width - 8
                        height: trow.implicitHeight + (page.expanded === model.id ? why.implicitHeight + 34 : 22)
                        radius: 12
                        color: page.expanded === model.id ? Theme.surface2 : (tma.containsMouse ? Qt.rgba(1, 1, 1, 0.025) : "transparent")
                        Behavior on height { NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }
                        clip: true
                        RowLayout {
                            id: trow
                            x: 12; y: 11
                            width: parent.width - 24
                            spacing: 0
                            Txt { text: model.entryTime; color: Theme.textMuted; font.pixelSize: 13; Layout.preferredWidth: 120; Layout.minimumWidth: 120; Layout.maximumWidth: 120 }
                            Txt { text: model.symbol; font.family: Theme.display; font.weight: Font.Bold; font.pixelSize: 14; Layout.preferredWidth: 80; Layout.minimumWidth: 80; Layout.maximumWidth: 80 }
                            Txt { text: model.strategy; color: Theme.textDim; font.pixelSize: 13; Layout.preferredWidth: 170; Layout.minimumWidth: 170; Layout.maximumWidth: 170 }
                            Txt { text: Theme.money(model.entryPrice) + " → " + Theme.money(model.exitPrice); font.pixelSize: 13; Layout.preferredWidth: 170; Layout.minimumWidth: 170; Layout.maximumWidth: 170 }
                            Txt { text: model.heldMinutes + "m"; color: Theme.textDim; font.pixelSize: 13; Layout.preferredWidth: 60; Layout.minimumWidth: 60; Layout.maximumWidth: 60 }
                            Txt { text: model.exitReason; color: Theme.textDim; font.pixelSize: 13; Layout.fillWidth: true }
                            ColumnLayout {
                                Layout.preferredWidth: 110; Layout.minimumWidth: 110; Layout.maximumWidth: 110
                                spacing: 0
                                Txt { text: Theme.signedMoney(model.pnl); color: Theme.pnlColor(model.pnl); font.family: Theme.display; font.pixelSize: 14; Layout.alignment: Qt.AlignRight }
                                Txt { text: Theme.signedPct(model.pnlPct); color: Theme.pnlColor(model.pnl); font.pixelSize: 11; Layout.alignment: Qt.AlignRight }
                            }
                        }
                        Txt {
                            id: why
                            visible: page.expanded === model.id
                            anchors.top: trow.bottom
                            anchors.topMargin: 10
                            x: 12
                            width: parent.width - 24
                            text: "why it was bought: " + model.reasoning + "\nfees: " + Theme.money(model.fees) + " · decided by: " + (model.decisionMode === "rules" ? "rules only" : "rules + AI") + " · " + Theme.fixed(model.qty, 6) + " shares"
                            color: Theme.textDim
                            font.pixelSize: 13
                            wrapMode: Text.WordWrap
                            elide: Text.ElideNone
                        }
                        MouseArea { id: tma; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: page.expanded = page.expanded === model.id ? -1 : model.id }
                    }
                }
                EmptyState {
                    visible: app.tradesModel.count === 0
                    Layout.alignment: Qt.AlignHCenter
                    Layout.topMargin: -parent.height / 2
                    icon: "trades"
                    title: "no completed trades yet"
                    message: "when trades close, each one shows here with its entry, exit, result and the reasoning behind it."
                }
            }

            // ---------------- orders
            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 20
                visible: page.tab === "orders"
                spacing: 8
                RowLayout {
                    Layout.fillWidth: true
                    Layout.leftMargin: 12
                    spacing: 0
                    SectionLabel { text: "time"; font.pixelSize: 10; Layout.preferredWidth: 150; Layout.minimumWidth: 150; Layout.maximumWidth: 150 }
                    SectionLabel { text: "stock"; font.pixelSize: 10; Layout.preferredWidth: 80; Layout.minimumWidth: 80; Layout.maximumWidth: 80 }
                    SectionLabel { text: "side"; font.pixelSize: 10; Layout.preferredWidth: 80; Layout.minimumWidth: 80; Layout.maximumWidth: 80 }
                    SectionLabel { text: "type"; font.pixelSize: 10; Layout.preferredWidth: 130; Layout.minimumWidth: 130; Layout.maximumWidth: 130 }
                    SectionLabel { text: "status"; font.pixelSize: 10; Layout.preferredWidth: 130; Layout.minimumWidth: 130; Layout.maximumWidth: 130 }
                    SectionLabel { text: "filled"; font.pixelSize: 10; Layout.preferredWidth: 160; Layout.minimumWidth: 160; Layout.maximumWidth: 160 }
                    SectionLabel { text: "notes"; font.pixelSize: 10; Layout.fillWidth: true }
                }
                ListView {
                    id: olist
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    model: app.ordersModel
                    boundsBehavior: Flickable.StopAtBounds
                    ScrollBar.vertical: ScrollBar { contentItem: Rectangle { implicitWidth: 6; radius: 3; color: Theme.borderStrong } }
                    delegate: RowLayout {
                        width: olist.width - 20
                        x: 12
                        height: 40
                        spacing: 0
                        Txt { text: model.timeText; color: Theme.textMuted; font.pixelSize: 12; Layout.preferredWidth: 150; Layout.minimumWidth: 150; Layout.maximumWidth: 150 }
                        Txt { text: model.symbol; font.weight: Font.Bold; font.pixelSize: 13; Layout.preferredWidth: 80; Layout.minimumWidth: 80; Layout.maximumWidth: 80 }
                        Item { Layout.preferredWidth: 80; Layout.minimumWidth: 80; Layout.maximumWidth: 80; implicitHeight: 22; ActionChip { action: model.side; anchors.verticalCenter: parent.verticalCenter } }
                        Txt { text: model.type + (model.purpose === "stop" ? " (protective)" : model.purpose === "entry" ? " (entry)" : " (exit)") + (model.stopPrice > 0 ? " @" + Theme.money(model.stopPrice) : model.limitPrice > 0 ? " @" + Theme.money(model.limitPrice) : ""); color: Theme.textDim; font.pixelSize: 12; Layout.preferredWidth: 130; Layout.minimumWidth: 130; Layout.maximumWidth: 130 }
                        Item { Layout.preferredWidth: 130; Layout.minimumWidth: 130; Layout.maximumWidth: 130; implicitHeight: 22
                            Pill { anchors.verticalCenter: parent.verticalCenter; text: model.status.replace("_", " "); kind: model.status === "filled" ? "lime" : model.status === "rejected" || model.status === "abandoned" ? "sell" : model.status === "canceled" || model.status === "expired" ? "neutral" : "info" } }
                        Txt { text: Theme.fixed(model.filledQty, 6) + " / " + Theme.fixed(model.qty, 6) + (model.price > 0 ? " @ " + Theme.money(model.price) : ""); font.pixelSize: 12; Layout.preferredWidth: 160; Layout.minimumWidth: 160; Layout.maximumWidth: 160 }
                        Txt { text: model.rejectReason; color: Theme.amber; font.pixelSize: 12; Layout.fillWidth: true }
                    }
                }
                EmptyState {
                    visible: app.ordersModel.count === 0
                    Layout.alignment: Qt.AlignHCenter
                    Layout.topMargin: -parent.height / 2
                    icon: "trades"
                    title: "no orders yet"
                    message: "every order sent to the broker — entries, protective stops and exits — is listed here."
                }
            }
        }
    }
}
