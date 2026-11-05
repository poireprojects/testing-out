import QtQuick
import QtQuick.Shapes

Item {
    id: root
    property string name: ""
    property color color: Theme.text
    property real size: 18
    property real stroke: 1.8
    implicitWidth: size
    implicitHeight: size
    layer.enabled: true
    layer.samples: 8
    layer.smooth: true

    Shape {
        width: 24; height: 24
        scale: root.size / 24
        transformOrigin: Item.TopLeft
        ShapePath {
            strokeColor: root.color
            strokeWidth: root.stroke * 24 / root.size
            fillColor: "transparent"
            capStyle: ShapePath.RoundCap
            joinStyle: ShapePath.RoundJoin
            PathSvg { path: Theme.icons[root.name] || "" }
        }
    }
}
