import QtQuick
import QtQuick.Layouts

Rectangle {
    id: root
    property string label: ""
    property string tip: ""
    property alias valueText: main.text
    property color valueColor: Theme.text
    property string sub: ""
    property color subColor: Theme.textMuted
    property string icon: ""
    default property alias extra: extraBox.data
    color: Theme.surface
    radius: Theme.radius
    border.width: 1
    border.color: Theme.border
    implicitHeight: 118
    implicitWidth: 200

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 18
        spacing: 6
        RowLayout {
            spacing: 6
            Icon { visible: root.icon !== ""; name: root.icon; size: 14; color: Theme.textMuted }
            Txt { text: root.label; color: Theme.textDim; font.pixelSize: 13; font.weight: Font.Medium }
            InfoTip { term: root.tip; visible: root.tip !== "" }
            Item { Layout.fillWidth: true }
        }
        Item { Layout.fillHeight: true }
        Txt {
            id: main
            font.family: Theme.display
            font.pixelSize: 26
            font.weight: Font.Medium
            color: root.valueColor
            Layout.fillWidth: true
            fontSizeMode: Text.HorizontalFit
            minimumPixelSize: 15
            elide: Text.ElideNone
        }
        Txt {
            visible: root.sub !== ""
            text: root.sub
            color: root.subColor
            font.pixelSize: 12
            Layout.fillWidth: true
        }
    }
    Item { id: extraBox; anchors.fill: parent }
}
