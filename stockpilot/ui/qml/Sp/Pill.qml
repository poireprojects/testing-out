import QtQuick

Rectangle {
    id: root
    property string text: ""
    property string kind: "neutral"   // buy | sell | hold | info | warn | lime | neutral | live | solidLime
    property string icon: ""
    property int fontSize: 11
    readonly property color fg: kind === "buy" || kind === "lime" ? Theme.lime
                              : kind === "sell" || kind === "live" ? Theme.coral
                              : kind === "warn" ? Theme.amber
                              : kind === "info" ? Theme.sky
                              : kind === "solidLime" ? Theme.limeInk
                              : Theme.slate
    readonly property color bgc: kind === "buy" || kind === "lime" ? Theme.limeSoft
                               : kind === "sell" || kind === "live" ? Theme.coralSoft
                               : kind === "warn" ? Theme.amberSoft
                               : kind === "info" ? Theme.skySoft
                               : kind === "solidLime" ? Theme.lime
                               : Theme.slateSoft
    implicitHeight: fontSize + 11
    implicitWidth: row.implicitWidth + 16
    radius: height / 2
    color: bgc
    Behavior on color { ColorAnimation { duration: 200 } }

    Row {
        id: row
        anchors.centerIn: parent
        spacing: 4
        Icon { visible: root.icon !== ""; name: root.icon; size: root.fontSize + 1; color: root.fg; stroke: 2.2; anchors.verticalCenter: parent.verticalCenter }
        Txt {
            text: root.text
            color: root.fg
            font.pixelSize: root.fontSize
            font.weight: Font.DemiBold
            font.letterSpacing: 0.3
            anchors.verticalCenter: parent.verticalCenter
        }
    }
}
