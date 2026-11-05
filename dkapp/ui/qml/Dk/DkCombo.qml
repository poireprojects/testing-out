import QtQuick
import QtQuick.Controls.Basic

ComboBox {
    id: root
    hoverEnabled: true
    implicitHeight: 40
    implicitWidth: 240
    font.family: Theme.body
    font.pixelSize: 14
    leftPadding: 14
    contentItem: Txt {
        text: root.displayText
        color: Theme.text
        font: root.font
        leftPadding: 0
        rightPadding: 28
    }
    indicator: Icon {
        name: "down"
        size: 14
        color: Theme.textDim
        x: root.width - width - 12
        y: (root.height - height) / 2
        rotation: root.popup.visible ? 180 : 0
        Behavior on rotation { NumberAnimation { duration: 150 } }
    }
    background: Rectangle {
        radius: 11
        color: Theme.bg
        border.width: 1
        border.color: root.popup.visible || root.activeFocus ? Theme.lime : (root.hovered ? Theme.borderStrong : Theme.border)
    }
    delegate: ItemDelegate {
        id: d
        width: root.width - 8
        x: 4
        hoverEnabled: true
        contentItem: Txt {
            text: root.textRole ? (Array.isArray(root.model) ? modelData[root.textRole] : model[root.textRole]) : modelData
            color: root.currentIndex === index ? Theme.lime : Theme.text
            font.pixelSize: 13
        }
        background: Rectangle { radius: 8; color: d.hovered ? Theme.surface3 : "transparent" }
    }
    popup: Popup {
        y: root.height + 6
        width: root.width
        implicitHeight: Math.min(contentItem.implicitHeight + 8, 320)
        padding: 4
        contentItem: ListView {
            clip: true
            implicitHeight: contentHeight
            model: root.popup.visible ? root.delegateModel : null
            currentIndex: root.highlightedIndex
        }
        background: Rectangle { radius: 12; color: Theme.surface2; border.color: Theme.borderStrong }
        enter: Transition { NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 120 } }
    }
}
