import QtQuick
import QtQuick.Controls.Basic

// Page scroller with a slim, rounded scrollbar.
Flickable {
    id: root
    default property alias content: inner.data
    property real maxContentWidth: 1400
    property real margin: 28
    clip: true
    contentWidth: width
    contentHeight: inner.implicitHeight + margin * 2
    boundsBehavior: Flickable.StopAtBounds
    flickableDirection: Flickable.VerticalFlick
    Item {
        id: inner
        x: Math.max(root.margin, (root.width - width) / 2)
        y: root.margin
        width: Math.min(root.maxContentWidth, root.width - root.margin * 2)
        implicitHeight: childrenRect.height
    }
    ScrollBar.vertical: ScrollBar {
        id: sb
        policy: root.contentHeight > root.height ? ScrollBar.AsNeeded : ScrollBar.AlwaysOff
        contentItem: Rectangle {
            implicitWidth: 6
            radius: 3
            color: sb.pressed ? Theme.textMuted : Theme.borderStrong
            opacity: sb.active ? 1 : 0.5
        }
        background: Item {}
    }
}
