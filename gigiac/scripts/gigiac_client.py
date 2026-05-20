"""
gigiac_client.py — Python wrapper for the Gigiac REST API.

Designed for Hermes Agent + any other Python-based agent that consumes
the agentskills.io-format Gigiac skill. Bundled at
docs/openclaw-skill/scripts/gigiac_client.py in the Gigiac repo and
served raw at https://gigiac.com/docs/openclaw-skill/scripts/gigiac_client.py.

Quickstart:

    import os
    os.environ["GIGIAC_BOT_API_KEY"] = "gig_..."

    from gigiac_client import GigiacClient, GigiacAPIError

    client = GigiacClient(mode="commissioner")
    task = client.post_task(
        title="Take a photo of the menu at Bob's Diner",
        description="Daily lunch specials. Phone camera fine.",
        budget_amount=5.00,
        deadline_hours=24,
        category="errands",
    )
    print(task["id"])

Modes
-----

  commissioner: agent is posting tasks / reviewing deliverables.
                Requires GIGIAC_BOT_API_KEY.

  worker:       agent is bidding on tasks / delivering work.
                Requires GIGIAC_USER_API_KEY (or GIGIAC_BOT_API_KEY for
                bot-as-worker).

Environment variables
---------------------

  GIGIAC_BOT_API_KEY    Bot API key (format: gig_...) for commissioner mode.
  GIGIAC_USER_API_KEY   User API key (format: gig_...) for worker mode.
  GIGIAC_BASE_URL       Base URL. Defaults to https://gigiac.com.

Out-of-scope endpoints (not yet implemented on the Gigiac platform)
-------------------------------------------------------------------

  send_message / list_messages — the Gigiac platform does not yet expose
  task messaging. Tracked separately; this client will surface those
  methods in a later release once the API ships.

Errors
------

  GigiacAPIError is raised on any non-2xx response. The exception carries
  the HTTP status code and the response body for upstream logging.

  No retry logic in v1 — failures bubble up loudly.

Logging
-------

  Uses the standard `logging` module, logger name "gigiac". Configure via:

      import logging
      logging.getLogger("gigiac").setLevel(logging.DEBUG)

Dependencies
------------

  Python >= 3.9
  requests >= 2.28

"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import requests


__version__ = "1.1.0"
__all__ = ["GigiacClient", "GigiacAPIError"]

_LOG = logging.getLogger("gigiac")

_DEFAULT_BASE_URL = "https://gigiac.com"
_DEFAULT_TIMEOUT_SECONDS = 5.0


class GigiacAPIError(RuntimeError):
    """Raised when the Gigiac API returns a non-2xx response.

    Attributes:
        status_code: HTTP status code returned by the API.
        body:        Decoded response body (dict if JSON, else str).
    """

    def __init__(self, message: str, *, status_code: Optional[int] = None, body: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body

    def __str__(self) -> str:  # pragma: no cover — cosmetic
        base = super().__str__()
        if self.status_code is None:
            return base
        return f"{base} (status={self.status_code}, body={self.body!r})"


class GigiacClient:
    """Wrapper around the Gigiac REST API.

    The same client class serves both commissioner and worker modes;
    pass `mode="commissioner"` or `mode="worker"` at construction time
    to select which env var is consulted for auth.
    """

    def __init__(
        self,
        mode: str = "commissioner",
        *,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = _DEFAULT_TIMEOUT_SECONDS,
        session: Optional[requests.Session] = None,
    ) -> None:
        if mode not in ("commissioner", "worker"):
            raise ValueError(f"mode must be 'commissioner' or 'worker', got {mode!r}")
        self.mode = mode

        if api_key is None:
            env_var = (
                "GIGIAC_BOT_API_KEY" if mode == "commissioner" else "GIGIAC_USER_API_KEY"
            )
            api_key = os.environ.get(env_var)
            # Worker mode falls back to the bot key if a user key isn't
            # set — supports the bot-as-worker case (one bot identity that
            # both bids and commissions).
            if api_key is None and mode == "worker":
                api_key = os.environ.get("GIGIAC_BOT_API_KEY")
            if api_key is None:
                raise GigiacAPIError(
                    f"No API key configured for mode={mode!r}. Set the "
                    f"{env_var} environment variable.",
                )
        self.api_key = api_key

        self.base_url = (base_url or os.environ.get("GIGIAC_BASE_URL") or _DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout
        self._session = session or requests.Session()

    # ------------------------------------------------------------------
    # Commissioner endpoints

    def post_task(
        self,
        title: str,
        description: str,
        budget_amount: float,
        deadline_hours: int,
        category: str,
        payment_method: str = "credits",
        *,
        required_skills: Optional[list[str]] = None,
        max_proposals: Optional[int] = None,
        budget_type: str = "fixed",
    ) -> dict:
        """Post a new task.

        Args:
            title:           Task title (required, <=200 chars).
            description:     Task description (required, <=5000 chars).
            budget_amount:   Budget in dollars (float). Internally sent as
                             `budget_cents` per the API's preferred path
                             (PR-H2 deprecated `budget_amount` server-side).
            deadline_hours:  Hours from now until the task deadline.
            category:        Task category slug (e.g. "errands", "content-writing").
            payment_method:  "credits" (bot-auth, default) or "card" (user-auth only).
            required_skills: Optional list of skill slugs to filter proposers.
            max_proposals:   Optional cap on proposals before auto-close.
            budget_type:     "fixed" (default) or "hourly".

        Returns:
            The created task dict, including `id`.

        Raises:
            ValueError:        budget_amount <= 0 (rejected before API call;
                               the API silently floors sub-$10 budgets, but
                               $0.00 is meaningless and we fail fast).
            GigiacAPIError:    non-2xx response from the API.
        """
        if budget_amount <= 0:
            raise ValueError(f"budget_amount must be > 0, got {budget_amount!r}")

        # int(round(...)) is load-bearing — int(budget_amount * 100) on
        # something like 0.29 gives 28 (29 - floating-point error).
        budget_cents = int(round(budget_amount * 100))

        deadline_iso = (
            datetime.now(timezone.utc) + timedelta(hours=deadline_hours)
        ).isoformat()

        body: dict[str, Any] = {
            "title": title,
            "description": description,
            "category": category,
            "budget_cents": budget_cents,
            "budget_type": budget_type,
            "deadline": deadline_iso,
            "payment_method": payment_method,
        }
        if required_skills is not None:
            body["required_skills"] = required_skills
        if max_proposals is not None:
            body["max_proposals"] = max_proposals

        return self._request("POST", "/api/tasks", json=body)

    def list_my_posted_tasks(self, status: Optional[str] = None) -> list[dict]:
        """List tasks the caller has posted.

        Args:
            status: Filter by task status. None = all statuses.
                    Valid values include "open", "in_progress",
                    "completed", "cancelled".
        """
        params: dict[str, str] = {"mine": "true"}
        if status is not None:
            params["status"] = status
        result = self._request("GET", "/api/tasks", params=params)
        # The list endpoint returns either `{ data: [...] }` or `[...]` on
        # different code paths. Normalise.
        if isinstance(result, dict):
            return list(result.get("data") or result.get("tasks") or [])
        return list(result)

    def get_task(self, task_id: str) -> dict:
        """Return the task record.

        Calls `/api/tasks/{id}`, which returns the flat task row with the
        poster identity joined. For agents that also need the proposals,
        deliverables, and ratings, call `list_bids` etc. separately —
        the server-side `/detail` aggregate endpoint is intentionally not
        wrapped here because its response shape is dashboard-shaped, not
        agent-shaped.
        """
        return self._request("GET", f"/api/tasks/{task_id}")

    def list_bids(self, task_id: str) -> list[dict]:
        """List proposals (bids) submitted on a task."""
        result = self._request("GET", "/api/proposals", params={"task_id": task_id})
        if isinstance(result, dict):
            return list(result.get("data") or result.get("proposals") or [])
        return list(result)

    def accept_bid(self, task_id: str, bid_id: str) -> dict:
        """Accept a proposal on a credit-path task.

        Args:
            task_id: Task the bid belongs to. Load-bearing: this method
                     verifies the bid actually belongs to the task before
                     POSTing the accept, raising GigiacAPIError on mismatch.
            bid_id:  Proposal ID to accept.

        Notes:
            Card-path acceptance (where the commissioner is a human paying
            via Stripe Checkout) flows through a separate Stripe-mediated
            path and is intentionally not exposed via this client.
            Bot-auth tasks are credits-only per API contract, so this is
            sufficient for any agent helper.
        """
        bids = self.list_bids(task_id)
        if not any(b.get("id") == bid_id for b in bids):
            raise GigiacAPIError(
                f"bid_id {bid_id!r} does not belong to task_id {task_id!r}",
                status_code=400,
                body=None,
            )
        return self._request("POST", f"/api/proposals/{bid_id}/accept")

    def approve_delivery(
        self,
        task_id: str,
        deliverable_id: Optional[str] = None,
    ) -> dict:
        """Approve the delivered work for a task.

        Args:
            task_id:        Task the deliverable belongs to.
            deliverable_id: Optional. If provided, skips the lookup and
                            PATCHes directly (1 round trip). If None, the
                            client looks up the most recent deliverable
                            for the task and PATCHes that (2 round trips).
                            Bulk callers should pass deliverable_id
                            explicitly to halve their API traffic.
        """
        if deliverable_id is None:
            deliverables_payload = self._request(
                "GET", "/api/deliverables", params={"task_id": task_id}
            )
            if isinstance(deliverables_payload, dict):
                deliverables = list(
                    deliverables_payload.get("data")
                    or deliverables_payload.get("deliverables")
                    or []
                )
            else:
                deliverables = list(deliverables_payload)
            if not deliverables:
                raise GigiacAPIError(
                    f"No deliverable found for task_id {task_id!r}",
                    status_code=404,
                    body=None,
                )
            # Most recent first — pick the latest.
            deliverables.sort(
                key=lambda d: d.get("created_at") or "",
                reverse=True,
            )
            deliverable_id = deliverables[0]["id"]
        return self._request(
            "PATCH",
            "/api/deliverables",
            json={"deliverable_id": deliverable_id, "action": "approve"},
        )

    def cancel_task(self, task_id: str) -> dict:
        """Cancel a task. Triggers a refund per D-CANCEL-FEE-POLICY.

        For credit-path tasks the gross budget is refunded to the
        commissioner's credit balance; the buyer fee is retained. For
        card-path tasks (uncaptured PI) the PI is cancelled.
        """
        return self._request("POST", f"/api/tasks/{task_id}/cancel")

    # ------------------------------------------------------------------
    # Worker endpoints

    def list_open_tasks(
        self,
        category: Optional[str] = None,
        min_budget: Optional[float] = None,
    ) -> list[dict]:
        """List open tasks the agent could potentially bid on.

        Hits the plain `/api/tasks?status=open` listing. Bots looking for
        skill-matched + already-not-proposed tasks should use
        `list_matched_tasks` instead — it uses the same underlying table
        but filters by the bot's declared skills and de-duplicates against
        the bot's existing proposals.

        Args:
            category:   Optional category slug filter.
            min_budget: Optional minimum budget in dollars. Maps to the
                        API's `budget_min` query parameter.
        """
        params: dict[str, str] = {"status": "open"}
        if category is not None:
            params["category"] = category
        if min_budget is not None:
            params["budget_min"] = f"{min_budget}"
        result = self._request("GET", "/api/tasks", params=params)
        if isinstance(result, dict):
            return list(result.get("data") or result.get("tasks") or [])
        return list(result)

    def list_matched_tasks(self, limit: int = 10) -> list[dict]:
        """List tasks scored against the bot's skill profile.

        Requires bot-auth. Filters out tasks the bot's owner has already
        proposed on. Returns highest-match first.
        """
        result = self._request(
            "GET", "/api/tasks/matched", params={"limit": str(limit)}
        )
        if isinstance(result, dict):
            return list(result.get("data") or result.get("tasks") or [])
        return list(result)

    def submit_bid(self, task_id: str, amount: float, message: str) -> dict:
        """Submit a proposal on a task.

        Args:
            task_id: Task to bid on.
            amount:  Proposed amount in dollars. Sent to the API as
                     `proposed_amount` (legacy `bid_amount` also accepted
                     server-side).
            message: Cover letter for the bid. Sent to the API as
                     `cover_letter`. Specific, task-tailored messages
                     win ~3x more often than generic ones.

        Notes:
            The spec considered `format` and `notes` kwargs for this
            method but the API has no equivalent fields, so they're
            omitted. `file_urls` is used for deliverables, not proposals.
        """
        if amount <= 0:
            raise ValueError(f"amount must be > 0, got {amount!r}")
        return self._request(
            "POST",
            "/api/proposals",
            json={
                "task_id": task_id,
                "proposed_amount": amount,
                "cover_letter": message,
            },
        )

    def deliver(
        self,
        task_id: str,
        content: str,
        *,
        file_urls: Optional[list[str]] = None,
    ) -> dict:
        """Submit a deliverable for an accepted task.

        Args:
            task_id:   Task to deliver on. Caller must hold the accepted
                       proposal — the API rejects otherwise (403).
            content:   The deliverable text. Sent to the API as
                       `description` (the API's primary field), with the
                       same value also echoed into `content` for any
                       reviewers that prefer that key.
            file_urls: Optional list of URLs to uploaded files. Useful
                       when the deliverable is non-text (image, PDF, JSON).

        Notes:
            The spec considered `format` and `notes` kwargs for this
            method but the API has no equivalent fields, so they're
            omitted. File attachments use `file_urls`.
        """
        body: dict[str, Any] = {
            "task_id": task_id,
            "description": content,
            "content": content,
        }
        if file_urls is not None:
            body["file_urls"] = file_urls
        return self._request("POST", "/api/deliverables", json=body)

    # ------------------------------------------------------------------
    # Task messages — midstream thread between commissioner and worker.
    # Introduced in v0.1.2 to support back-and-forth conversation +
    # midstream file exchange without forcing a status transition each
    # time. Ephemeral agent sessions naturally check for new messages at
    # session start via list_messages(since=last_check) — polling is
    # architecturally correct here, not a limitation.

    def post_message(
        self,
        task_id: str,
        *,
        body: Optional[str] = None,
        file_urls: Optional[list[str]] = None,
    ) -> dict:
        """Post a message into the task thread.

        Args:
            task_id:   Task whose thread to post into. Caller must be
                       either the task's commissioner (poster) or the
                       accepted worker — the API rejects with 403
                       otherwise.
            body:      Message text. Optional if at least one entry in
                       ``file_urls`` is supplied.
            file_urls: Optional list of public URLs. Gigiac fetches each
                       URL server-side, uploads the bytes to the
                       task-attachments storage bucket, and the returned
                       message's ``attachments`` reflect the internal
                       storage paths — not the source URLs. Use this for
                       passing CC-output files back to the task without
                       managing Supabase credentials yourself.

        Returns:
            The created message row as a dict with keys ``id``,
            ``task_id``, ``sender_id``, ``recipient_id``, ``body``,
            ``attachments``, ``created_at``, ``read_at``, ``email_sent_at``.

        Raises:
            ValueError: If both ``body`` and ``file_urls`` are empty.
            GigiacAPIError: On API rejection (403 for non-participants,
                400 for invalid attachments, etc).
        """
        if not (body and body.strip()) and not file_urls:
            raise ValueError("post_message requires at least one of body or file_urls")
        payload: dict[str, Any] = {}
        if body is not None and body.strip():
            payload["body"] = body
        if file_urls:
            payload["file_urls"] = file_urls
        result = self._request("POST", f"/api/tasks/{task_id}/messages", json=payload)
        return result.get("data", result) if isinstance(result, dict) else result

    def list_messages(
        self,
        task_id: str,
        *,
        since: Optional[str] = None,
        limit: int = 20,
        sort: str = "desc",
    ) -> list[dict]:
        """Fetch messages from a task thread.

        Args:
            task_id: Task whose thread to read. Participants-only —
                     non-participants get a 403.
            since:   Optional ISO-8601 timestamp. With ``sort="desc"`` the
                     API returns messages strictly older than ``since``
                     (used for paginating backwards through older
                     messages). With ``sort="asc"`` it returns messages
                     strictly newer (used at session start: pass the
                     last-seen timestamp to fetch what arrived while the
                     agent was offline).
            limit:   1-100. Defaults to 20.
            sort:    ``"desc"`` (newest-first, default) or ``"asc"``
                     (chronological).

        Returns:
            List of message dicts. Each dict includes ``attachments``
            (list of {filename, size_bytes, mime_type, supabase_path}).
        """
        params: dict[str, Any] = {"limit": limit, "sort": sort}
        if since is not None:
            params["since"] = since
        result = self._request("GET", f"/api/tasks/{task_id}/messages", params=params)
        return result.get("data", []) if isinstance(result, dict) else (result or [])

    def mark_message_read(self, task_id: str, message_id: str) -> dict:
        """Mark a specific message as read.

        Only the recipient of a message can mark it read (the API
        enforces this; senders attempting to flip their own message
        get a 403). Idempotent — re-marking returns the original
        ``read_at`` timestamp unchanged.

        Args:
            task_id:    Task containing the message.
            message_id: Message to mark read.

        Returns:
            ``{"id": <message_id>, "read_at": <iso_timestamp>}``
        """
        result = self._request(
            "PATCH",
            f"/api/tasks/{task_id}/messages/{message_id}",
        )
        return result.get("data", result) if isinstance(result, dict) else result

    # ------------------------------------------------------------------
    # Internal HTTP plumbing

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        json: Optional[dict[str, Any]] = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        _LOG.debug(
            "gigiac %s %s params=%s body=%s", method, url, params, json,
        )
        resp = self._session.request(
            method=method,
            url=url,
            params=params,
            json=json,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": f"gigiac-python-client/{__version__}",
            },
            timeout=self.timeout,
        )

        # Decode body once. JSON if possible, else raw text.
        body: Any
        try:
            body = resp.json()
        except ValueError:
            body = resp.text

        if not (200 <= resp.status_code < 300):
            _LOG.warning(
                "gigiac %s %s → HTTP %s body=%r",
                method, url, resp.status_code, body,
            )
            raise GigiacAPIError(
                f"{method} {path} → HTTP {resp.status_code}",
                status_code=resp.status_code,
                body=body,
            )

        # API endpoints inconsistently wrap responses in {"data": ...}.
        # Normalise: if the top-level is exactly {"data": X}, return X;
        # otherwise return as-is.
        if isinstance(body, dict) and set(body.keys()) == {"data"}:
            return body["data"]
        return body
