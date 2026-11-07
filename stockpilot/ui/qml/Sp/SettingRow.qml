import QtQuick
import QtQuick.Layouts

RowLayout {
    id: root
    property string label: ""
    property string help: ""
    property string tip: ""
    default property alias control: slot.data
    spacing: 20
    Layout.fillWidth: true
    ColumnLayout {
        Layout.fillWidth: true
        spacing: 3
        RowLayout {
            spacing: 6
            Txt { text: root.label; font.pixelSize: 14; font.weight: Font.Medium }
            InfoTip { term: root.tip; visible: root.tip !== "" }
        }
        Txt {
            visible: root.help !== ""
            text: root.help
            color: Theme.textMuted
            font.pixelSize: 12
            wrapMode: Text.WordWrap
            elide: Text.ElideNone
            Layout.fillWidth: true
        }
    }
    Row { id: slot; spacing: 8; Layout.alignment: Qt.AlignVCenter }
}
