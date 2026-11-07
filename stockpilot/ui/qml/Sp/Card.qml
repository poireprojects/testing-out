import QtQuick
import QtQuick.Layouts

Rectangle {
    id: root
    default property alias content: body.data
    property string title: ""
    property string subtitle: ""
    property string tip: ""
    property string icon: ""
    property alias headerRight: headerRightRow.data
    property int pad: 20
    property bool hoverable: false
    property alias bodySpacing: body.spacing

    color: Theme.surface
    radius: Theme.radius
    border.width: 1
    border.color: hoverable && hover.hovered ? Theme.borderStrong : Theme.border
    implicitHeight: col.implicitHeight + pad * 2
    Behavior on border.color { ColorAnimation { duration: 160 } }

    HoverHandler { id: hover }

    ColumnLayout {
        id: col
        anchors.fill: parent
        anchors.margins: root.pad
        spacing: 14

        RowLayout {
            visible: root.title !== "" || headerRightRow.children.length > 0
            Layout.fillWidth: true
            spacing: 8
            Icon { visible: root.icon !== ""; name: root.icon; size: 16; color: Theme.textDim }
            Txt {
                text: root.title
                font.family: Theme.display
                font.pixelSize: 15
                font.weight: Font.Medium
                color: Theme.text
            }
            InfoTip { term: root.tip; visible: root.tip !== "" }
            Item { Layout.fillWidth: true }
            Row { id: headerRightRow; spacing: 8 }
        }
        Txt {
            visible: root.subtitle !== ""
            text: root.subtitle
            color: Theme.textMuted
            font.pixelSize: 13
            wrapMode: Text.WordWrap
            elide: Text.ElideNone
            Layout.fillWidth: true
            Layout.topMargin: -8
        }
        ColumnLayout {
            id: body
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 12
        }
    }
}
