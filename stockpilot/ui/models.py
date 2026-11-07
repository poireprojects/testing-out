"""A keyed list model for QML that updates in place (moves/inserts/removes/changes)
instead of resetting — so live lists re-rank smoothly and keep their scroll position."""
from __future__ import annotations

from typing import Any, Optional

from PySide6.QtCore import QAbstractListModel, QByteArray, QModelIndex, Qt, Property, Signal, Slot


class DictListModel(QAbstractListModel):
    countChanged = Signal()

    def __init__(self, roles: list[str], key: Optional[str] = None, parent=None):
        super().__init__(parent)
        self._roles = list(roles)
        self._role_ids = {Qt.ItemDataRole.UserRole + 1 + i: r for i, r in enumerate(self._roles)}
        self._key = key
        self._items: list[dict] = []

    def roleNames(self):
        return {k: QByteArray(v.encode()) for k, v in self._role_ids.items()}

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._items)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not (0 <= index.row() < len(self._items)):
            return None
        name = self._role_ids.get(role)
        if name is None:
            return None
        return self._items[index.row()].get(name)

    def _get_count(self) -> int:
        return len(self._items)

    count = Property(int, _get_count, notify=countChanged)

    @Slot(int, result="QVariantMap")
    def get(self, row: int) -> dict:
        return dict(self._items[row]) if 0 <= row < len(self._items) else {}

    def items(self) -> list[dict]:
        return list(self._items)

    def set_items(self, items: list[dict]) -> None:
        items = [dict(i) for i in items]
        before = len(self._items)
        if self._key is None:
            self.beginResetModel()
            self._items = items
            self.endResetModel()
        else:
            self._diff_update(items)
        if len(self._items) != before:
            self.countChanged.emit()

    def _diff_update(self, new: list[dict]) -> None:
        k = self._key
        new_keys = [i.get(k) for i in new]
        if len(set(new_keys)) != len(new_keys):  # duplicate keys: fall back to reset
            self.beginResetModel()
            self._items = new
            self.endResetModel()
            return
        wanted = set(new_keys)
        # 1) removals (from the bottom up)
        for row in range(len(self._items) - 1, -1, -1):
            if self._items[row].get(k) not in wanted:
                self.beginRemoveRows(QModelIndex(), row, row)
                del self._items[row]
                self.endRemoveRows()
        # 2) moves and inserts, walking the target order
        for target, item in enumerate(new):
            key = item.get(k)
            cur = next((r for r in range(target, len(self._items)) if self._items[r].get(k) == key), None)
            if cur is None:
                self.beginInsertRows(QModelIndex(), target, target)
                self._items.insert(target, item)
                self.endInsertRows()
                continue
            if cur != target:
                self.beginMoveRows(QModelIndex(), cur, cur, QModelIndex(), target)
                self._items.insert(target, self._items.pop(cur))
                self.endMoveRows()
            if self._items[target] != item:
                self._items[target] = item
                idx = self.index(target)
                self.dataChanged.emit(idx, idx)

    def prepend(self, items: list[dict], limit: int = 300) -> None:
        if not items:
            return
        self.beginInsertRows(QModelIndex(), 0, len(items) - 1)
        self._items[0:0] = [dict(i) for i in items]
        self.endInsertRows()
        if len(self._items) > limit:
            self.beginRemoveRows(QModelIndex(), limit, len(self._items) - 1)
            del self._items[limit:]
            self.endRemoveRows()
        self.countChanged.emit()
