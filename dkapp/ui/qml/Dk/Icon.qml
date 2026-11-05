import QtQuick
import DkCharts 1.0

Item {
    id: root
    property string name: ""
    property color color: Theme.text
    property real size: 18
    property real stroke: 1.8
    implicitWidth: size
    implicitHeight: size

    SvgIcon {
        anchors.fill: parent
        pathData: Theme.icons[root.name] || ""
        color: root.color
        strokeWidth: root.stroke * 24 / Math.max(1, root.size)
    }
}
