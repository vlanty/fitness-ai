"""`StateStore` on Firestore.

Layout:
  users/{user_id}                  profile{...}, settings{...}, pending{...}
  users/{user_id}/goals/active     the current goal (one at a time)
  users/{user_id}/usage/{YYYY-MM}  calls, input_tokens, output_tokens, cost_usd
  identities/{provider:id}         user_id   (e.g. identities/telegram:123)
"""

from __future__ import annotations


class FirestoreStore:
    def __init__(self, project: str, client=None) -> None:
        from google.cloud import firestore  # imported lazily: tests don't need the package

        self._fs = firestore
        self.db = client or firestore.Client(project=project)

    def _doc(self, user_id):
        if not user_id:
            raise ValueError("user_id is required")
        return self.db.collection("users").document(str(user_id))

    def get_user(self, user_id):
        snap = self._doc(user_id).get()
        return (snap.to_dict() or {}) if snap.exists else {}

    def update_profile(self, user_id, fields):
        data = {f"profile.{k}": (self._fs.DELETE_FIELD if v is None else v) for k, v in fields.items()}
        self._update(user_id, data)

    def clear_profile(self, user_id):
        self._update(user_id, {"profile": {}})

    def update_settings(self, user_id, fields):
        self._update(user_id, {f"settings.{k}": v for k, v in fields.items()})

    def set_pending(self, user_id, pending):
        self._update(user_id, {"pending": self._fs.DELETE_FIELD if pending is None else pending})

    def _update(self, user_id, data):
        doc = self._doc(user_id)
        try:
            doc.update(data)
        except Exception as e:  # NotFound on the first write for this user
            if type(e).__name__ != "NotFound":
                raise
            doc.set({"profile": {}, "settings": {}})
            doc.update(data)

    def get_goal(self, user_id):
        snap = self._doc(user_id).collection("goals").document("active").get()
        return snap.to_dict() if snap.exists else None

    def set_goal(self, user_id, goal):
        self._doc(user_id).collection("goals").document("active").set(goal)

    def delete_goal(self, user_id):
        self._doc(user_id).collection("goals").document("active").delete()

    def add_usage(self, user_id, month, input_tokens, output_tokens, cost_usd):
        inc = self._fs.Increment
        self._doc(user_id).collection("usage").document(month).set({
            "calls": inc(1), "input_tokens": inc(input_tokens),
            "output_tokens": inc(output_tokens), "cost_usd": inc(cost_usd),
        }, merge=True)

    def get_usage(self, user_id, month):
        snap = self._doc(user_id).collection("usage").document(month).get()
        return (snap.to_dict() or {}) if snap.exists else {}

    def update_guard(self, user_id, guard):
        self._update(user_id, {"guard": guard})

    def get_identity(self, key):
        snap = self.db.collection("identities").document(key).get()
        return (snap.to_dict() or {}).get("user_id") if snap.exists else None

    def link_identity(self, key, user_id):
        self.db.collection("identities").document(key).set({"user_id": user_id})
