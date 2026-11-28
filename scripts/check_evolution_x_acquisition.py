"""Exercise synthetic WebSocket acquisition, repeated reconnect and durable recovery."""

from argparse import ArgumentParser
from dataclasses import asdict
from pathlib import Path
from threading import Thread
from time import monotonic, sleep
import tracemalloc
import json

from websockets.sync.server import serve

from rail_vehicle.acquisition_store import append_packets, load_recording, open_recording
from rail_vehicle.db import init_db
from rail_vehicle.state_monitoring import simulated_stream
from rail_vehicle.stream_loopback import LoopbackCapture


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--frames", type=int, default=1000)
    args = parser.parse_args()
    args.directory.mkdir(parents=True, exist_ok=False)
    path = args.directory / "synthetic.sqlite"
    init_db(path)
    reference, sequence, _ = simulated_stream("damping", args.frames)
    packets = [packet for _, packet in sequence]
    packets.insert(80, packets[79])  # repeated frame
    late = next(index for index in range(86, len(packets)) if packets[index].device == packets[85].device)
    packets[85], packets[late] = packets[late], packets[85]
    for packet in packets:
        reference.push(packet)
    offsets = []

    def relay(channel):
        start = json.loads(channel.recv(timeout=3))["resume"]
        offsets.append(start)
        channel.send(json.dumps({"resume": start, "declaration": reference.declaration()}))
        stop = min(start + 173, len(packets))
        for index in range(start, stop):
            channel.send(json.dumps({"index": index, "packet": asdict(packets[index])}))
            if index % 3 == 0:
                sleep(.02)
        if stop == len(packets):
            channel.send(json.dumps({"end": stop}))

    capture = LoopbackCapture()
    record_id, offset = None, 0
    calls = []
    started = monotonic()
    tracemalloc.start()
    with serve(relay, "127.0.0.1", 0) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"ws://127.0.0.1:{server.socket.getsockname()[1]}/stream"
            for iteration in range(72):
                t0 = monotonic()
                status = capture.receive(url, budget=230)
                if record_id is None:
                    record_id = open_recording("合成实时采集", capture.record, path)
                offset = append_packets(record_id, offset, capture.record, path)
                calls.append({"count": offset, "state": status, "elapsed_s": monotonic() - t0})
                if capture.complete:
                    break
                if iteration in (1, 5):
                    capture = LoopbackCapture(load_recording(record_id, path))
        finally:
            server.shutdown()
            thread.join(timeout=2)
    persisted = load_recording(record_id, path)
    assert capture.complete and len(persisted.packets) == len(packets)
    assert persisted.evidence_id == reference.evidence_id and persisted.counters == reference.counters
    assert persisted.counters["repeated"] and persisted.counters["late"]
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    evidence = {"source": "synthetic_paced_loopback", "frames_requested": args.frames,
                "duration_s": monotonic() - started, "recorded_packets": offset,
                "counts": persisted.counters, "peak_python_bytes": peak, "live_python_bytes": current,
                "evidence_id": persisted.evidence_id, "resume_offsets": offsets, "calls": calls,
                "matches_monolithic_reference": True}
    (args.directory / "acquisition-proof.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(f"{offset} packets, {len(offsets)} connections, SHA evidence {persisted.evidence_id}")


if __name__ == "__main__":
    main()
