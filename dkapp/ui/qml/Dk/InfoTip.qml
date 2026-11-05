import QtQuick
import QtQuick.Controls.Basic

Item {
    id: root
    property string term: ""
    property string text: ""
    readonly property string body: text !== "" ? text : (term !== "" ? app.explain(term) : "")
    implicitWidth: 16
    implicitHeight: 16
    visible: body !== ""

    Rectangle {
        anchors.fill: parent
        radius: width / 2
        color: hh.hovered ? Theme.limeSoft : "transparent"
        border.width: 1
        border.color: hh.hovered ? Theme.lime : Theme.borderStrong
        Behavior on color { ColorAnimation { duration: 120 } }
        Txt {
            anchors.centerIn: parent
            text: "?"
            font.pixelSize: 10
            font.weight: Font.Bold
            color: hh.hovered ? Theme.lime : Theme.textMuted
        }
    }
    HoverHandler { id: hh; cursorShape: Qt.WhatsThisCursor }

    ToolTip {
        id: tip
        visible: hh.hovered
        delay: 180
        timeout: 20000
        y: root.height + 6
        x: -12
        padding: 12
        contentItem: Txt {
            text: (root.term !== "" ? root.term.toLowerCase() + " — " : "") + root.body
            wrapMode: Text.WordWrap
            elide: Text.ElideNone
            width: Math.min(300, implicitWidth)
            font.pixelSize: 13
            lineHeight: 1.25
            color: Theme.text
        }
        background: Rectangle {
            color: Theme.surface3
            radius: 12
            border.color: Theme.borderStrong
        }
        enter: Transition { NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 140 } }
        exit: Transition { NumberAnimation { property: "opacity"; from: 1; to: 0; duration: 100 } }
    }
}
