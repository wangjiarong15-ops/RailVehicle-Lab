"""Opt-in, read-only WebSocket capture from a local sensor bridge."""

import json
from urllib.parse import urlsplit

from rail_vehicle.measurement_stream import Packet, read_declaration
from rail_vehicle.virtual_trials import json_text


class LoopbackCapture:
    def __init__(self, record=None):
        self.record=record
        self.complete=False

    def receive(self,url,budget=1000):
        from websockets.exceptions import ConnectionClosed
        from websockets.sync.client import connect

        parsed=urlsplit(url)
        if (parsed.scheme!="ws" or parsed.hostname!="127.0.0.1" or not parsed.port or parsed.username
                or parsed.password or parsed.fragment or parsed.query):
            raise ValueError("只允许用户明确指定的 ws://127.0.0.1:端口 本机采集桥。")
        if type(budget) is not int or not 1<=budget<=9000:
            raise ValueError("单次消息读取预算无效。")
        if self.complete:
            return "complete"
        try:
            with connect(url,open_timeout=3,close_timeout=.3,max_size=4096,proxy=None) as channel:
                start=len(self.record.packets) if self.record is not None else 0
                channel.send(json_text({"resume":start}))
                try:
                    header=json.loads(channel.recv(timeout=2))
                    if set(header)!={"resume","declaration"} or header["resume"]!=start:
                        raise ValueError("采集桥未确认预期的续传位置。")
                    incoming=read_declaration(header["declaration"])
                    if self.record is None:
                        self.record=incoming
                    elif incoming.declaration()!=self.record.declaration():
                        raise ValueError("重新连接后采集源发生变化，拒绝拼接。")
                    for index in range(start,start+budget):
                        message=json.loads(channel.recv(timeout=2))
                        if message=={"end":index}:
                            self.complete=True
                            break
                        if set(message)!={"index","packet"} or type(message["index"]) is not int or message["index"]!=index:
                            raise ValueError("采集桥的消息顺序与续传声明不符。")
                        self.record.push(Packet(**message["packet"]))
                except (ConnectionClosed,TimeoutError):
                    return "disconnected"
            return "complete" if self.complete else "recording"
        except (TypeError,KeyError,RecursionError):
            raise ValueError("采集桥返回无效声明或数据。") from None
