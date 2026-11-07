import QtQuick
import QtQuick.Layouts

ColumnLayout {
    id: root
    property string icon: "moon"
    property string title: ""
    property string message: ""
    spacing: 10
    Rectangle {
        Layout.alignment: Qt.AlignHCenter
        width: 54; height: 54; radius: 27
        color: Theme.surface2
        border.color: Theme.border
        Icon { anchors.centerIn: parent; name: root.icon; size: 24; color: Theme.textDim }
        SequentialAnimation on y {
            loops: Animation.Infinite
            running: root.visible
            NumberAnimation { from: 0; to: -4; duration: 1600; easing.type: Easing.InOutSine }
            NumberAnimation { from: -4; to: 0; duration: 1600; easing.type: Easing.InOutSine }
        }
    }
    Txt {
        Layout.alignment: Qt.AlignHCenter
        text: root.title
        font.family: Theme.display
        font.pixelSize: 16
        font.weight: Font.Medium
    }
    Txt {
        Layout.alignment: Qt.AlignHCenter
        Layout.maximumWidth: 380
        text: root.message
        color: Theme.textMuted
        font.pixelSize: 13
        wrapMode: Text.WordWrap
        horizontalAlignment: Text.AlignHCenter
        elide: Text.ElideNone
    }
}
