import datetime
import json
import time
from abc import ABC, abstractmethod
from contextlib import contextmanager
from io import StringIO
from threading import Event, Lock, Thread
from typing import Generic, Iterator, Sequence, TypeVar

from .._protocol import (
    ExtContextData,
    ExtMessage,
    ExtParams,
)


class ExtContextLoader(ABC):
    @abstractmethod
    @contextmanager
    def load_context(self, params: ExtParams) -> Iterator[ExtContextData]:
        ...


T_MessageChannel = TypeVar("T_MessageChannel", bound="ExtMessageWriterChannel")


class ExtMessageWriter(ABC, Generic[T_MessageChannel]):
    @abstractmethod
    @contextmanager
    def open(self, params: ExtParams) -> Iterator[T_MessageChannel]:
        ...


class ExtMessageWriterChannel(ABC, Generic[T_MessageChannel]):
    @abstractmethod
    def write_message(self, message: ExtMessage) -> None:
        ...


class ExtParamLoader(ABC):
    @abstractmethod
    def load_context_params(self) -> ExtParams:
        ...

    @abstractmethod
    def load_messages_params(self) -> ExtParams:
        ...


T_BlobStoreMessageWriterChannel = TypeVar(
    "T_BlobStoreMessageWriterChannel", bound="ExtBlobStoreMessageWriterChannel"
)


class ExtBlobStoreMessageWriter(ExtMessageWriter[T_BlobStoreMessageWriterChannel]):
    def __init__(self, *, interval: float = 10):
        self.interval = interval

    @contextmanager
    def open(self, params: ExtParams) -> Iterator[T_BlobStoreMessageWriterChannel]:
        channel = self.make_channel(params)
        with channel.buffered_upload_loop():
            yield channel

    @abstractmethod
    def make_channel(self, params: ExtParams) -> T_BlobStoreMessageWriterChannel:
        ...


class ExtBlobStoreMessageWriterChannel(ExtMessageWriterChannel):
    def __init__(self, *, interval: float = 10):
        self._interval = interval
        self._lock = Lock()
        self._buffer = []
        self._counter = 1

    def write_message(self, message: ExtMessage) -> None:
        with self._lock:
            self._buffer.append(message)

    def flush_messages(self) -> Sequence[ExtMessage]:
        with self._lock:
            messages = list(self._buffer)
            self._buffer.clear()
            return messages

    @abstractmethod
    def upload_messages_chunk(self, payload: StringIO, index: int) -> None:
        ...

    @contextmanager
    def buffered_upload_loop(self) -> Iterator[None]:
        thread = None
        is_task_complete = Event()
        try:
            thread = Thread(target=self._upload_loop, args=(is_task_complete,), daemon=True)
            thread.start()
            yield
        finally:
            is_task_complete.set()
            if thread:
                thread.join(timeout=60)

    def _upload_loop(self, is_task_complete: Event) -> None:
        start_or_last_upload = datetime.datetime.now()
        while True:
            num_pending = len(self._buffer)
            now = datetime.datetime.now()
            if num_pending == 0 and is_task_complete.is_set():
                break
            elif is_task_complete.is_set() or (now - start_or_last_upload).seconds > self._interval:
                payload = "\n".join([json.dumps(message) for message in self.flush_messages()])
                self.upload_messages_chunk(StringIO(payload), self._counter)
                start_or_last_upload = now
                self._counter += 1
            time.sleep(1)