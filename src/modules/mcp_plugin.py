#!/usr/bin/env python3
"""
mcp_plugin.py - MCP插件系统模块

该模块提供了MCP（Model Context Protocol）和插件系统功能，支持外部工具的集成。
外部进程可以暴露工具，经过少量标准化后，代理可以像使用普通工具一样使用它们。

核心功能：
- CapabilityPermissionGate: 权限门，用于控制工具执行权限
- MCPClient: MCP客户端，用于与外部MCP服务器通信
- PluginLoader: 插件加载器，用于加载和管理插件
- MCPToolRouter: MCP工具路由器，用于将工具调用路由到正确的MCP服务器

使用场景：
- 集成外部工具和服务
- 通过插件扩展系统功能
- 统一管理本地和外部工具的权限
"""


import json
import os
import subprocess
import threading
from pathlib import Path


class CapabilityPermissionGate:
    """
    权限门类，用于控制本地工具和外部工具的执行权限
    
    核心功能：
    - 统一管理本地和外部工具的权限
    - 标准化工具意图
    - 根据风险等级决定是否需要用户确认
    """

    # 读取操作的前缀
    READ_PREFIXES = ("read", "list", "get", "show", "search", "query", "inspect")
    # 高风险操作的前缀
    HIGH_RISK_PREFIXES = ("delete", "remove", "drop", "shutdown")

    def __init__(self, mode: str = "default"):
        """
        初始化权限门
        
        参数：
        - mode: 权限模式，可选值为"default"或"auto"
        """
        self.mode = mode if mode in ("default", "auto") else "default"

    def normalize(self, tool_name: str, tool_input: dict) -> dict:
        """
        标准化工具意图
        
        参数：
        - tool_name: 工具名称
        - tool_input: 工具输入参数
        
        返回：
        - 标准化的意图字典
        """
        if tool_name.startswith("mcp__"):
            _, server_name, actual_tool = tool_name.split("__", 2)
            source = "mcp"
        else:
            server_name = None
            actual_tool = tool_name
            source = "native"

        lowered = actual_tool.lower()
        if actual_tool == "read_file" or lowered.startswith(self.READ_PREFIXES):
            risk = "read"
        elif actual_tool == "bash":
            command = tool_input.get("command", "")
            risk = "high" if any(
                token in command for token in ("rm -rf", "sudo", "shutdown", "reboot")
            ) else "write"
        elif lowered.startswith(self.HIGH_RISK_PREFIXES):
            risk = "high"
        else:
            risk = "write"

        return {
            "source": source,
            "server": server_name,
            "tool": actual_tool,
            "risk": risk,
        }

    def check(self, tool_name: str, tool_input: dict) -> dict:
        """
        检查工具执行权限
        
        参数：
        - tool_name: 工具名称
        - tool_input: 工具输入参数
        
        返回：
        - 权限检查结果
        """
        intent = self.normalize(tool_name, tool_input)

        if intent["risk"] == "read":
            return {"behavior": "allow", "reason": "Read capability", "intent": intent}

        if self.mode == "auto" and intent["risk"] != "high":
            return {
                "behavior": "allow",
                "reason": "Auto mode for non-high-risk capability",
                "intent": intent,
            }

        if intent["risk"] == "high":
            return {
                "behavior": "ask",
                "reason": "High-risk capability requires confirmation",
                "intent": intent,
            }

        return {
            "behavior": "ask",
            "reason": "State-changing capability requires confirmation",
            "intent": intent,
        }

    def ask_user(self, intent: dict, tool_input: dict) -> bool:
        """
        询问用户是否允许执行工具
        
        参数：
        - intent: 工具意图
        - tool_input: 工具输入参数
        
        返回：
        - 用户是否允许执行
        """
        preview = json.dumps(tool_input, ensure_ascii=False)[:200]
        source = (
            f"{intent['source']}:{intent['server']}/{intent['tool']}"
            if intent.get("server")
            else f"{intent['source']}:{intent['tool']}"
        )
        print(f"\n  [Permission] {source} risk={intent['risk']}: {preview}")
        try:
            answer = input("  Allow? (y/n): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        return answer in ("y", "yes")


class MCPClient:
    """
    MCP客户端类，用于与外部MCP服务器通信
    
    核心功能：
    - 启动和管理MCP服务器进程
    - 与MCP服务器交换消息
    - 获取服务器提供的工具列表
    - 调用服务器上的工具
    - 将MCP工具转换为代理工具格式
    """

    def __init__(self, server_name: str, command: str, args: list = None, env: dict = None):
        """
        初始化MCP客户端
        
        参数：
        - server_name: 服务器名称
        - command: 启动服务器的命令
        - args: 命令参数（可选）
        - env: 环境变量（可选）
        """
        self.server_name = server_name
        self.command = command
        self.args = args or []
        self.env = {**os.environ, **(env or {})}
        self.process = None
        self._request_id = 0
        self._tools = []  # 缓存的工具列表

    def connect(self):
        """
        连接到MCP服务器
        
        返回：
        - 是否连接成功
        """
        try:
            self.process = subprocess.Popen(
                [self.command] + self.args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=self.env,
                text=True,
            )
            # 发送初始化请求
            self._send({"method": "initialize", "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "teaching-agent", "version": "1.0"},
            }})
            response = self._recv()
            if response and "result" in response:
                # 发送初始化完成通知
                self._send({"method": "notifications/initialized"})
                return True
        except FileNotFoundError:
            print(f"[MCP] Server command not found: {self.command}")
        except Exception as e:
            print(f"[MCP] Connection failed: {e}")
        return False

    def list_tools(self) -> list:
        """
        获取服务器提供的工具列表
        
        返回：
        - 工具列表
        """
        self._send({"method": "tools/list", "params": {}})
        response = self._recv()
        if response and "result" in response:
            self._tools = response["result"].get("tools", [])
        return self._tools

    def call_tool(self, tool_name: str, arguments: dict) -> str:
        """
        调用服务器上的工具
        
        参数：
        - tool_name: 工具名称
        - arguments: 工具参数
        
        返回：
        - 工具执行结果
        """
        self._send({"method": "tools/call", "params": {
            "name": tool_name,
            "arguments": arguments,
        }})
        response = self._recv()
        if response and "result" in response:
            content = response["result"].get("content", [])
            return "\n".join(c.get("text", str(c)) for c in content)
        if response and "error" in response:
            return f"MCP Error: {response['error'].get('message', 'unknown')}"
        return "MCP Error: no response"

    def get_agent_tools(self) -> list:
        """
        将MCP工具转换为代理工具格式
        
        返回：
        - 代理工具格式的工具列表
        """
        agent_tools = []
        for tool in self._tools:
            prefixed_name = f"mcp__{self.server_name}__{tool['name']}"
            agent_tools.append({
                "name": prefixed_name,
                "description": tool.get("description", ""),
                "input_schema": tool.get("inputSchema", {"type": "object", "properties": {}}),
                "_mcp_server": self.server_name,
                "_mcp_tool": tool["name"],
            })
        return agent_tools

    def disconnect(self):
        """
        断开与MCP服务器的连接
        """
        if self.process:
            try:
                self._send({"method": "shutdown"})
                self.process.terminate()
                self.process.wait(timeout=5)
            except Exception:
                self.process.kill()
            self.process = None

    def _send(self, message: dict):
        """
        发送消息到MCP服务器
        
        参数：
        - message: 消息内容
        """
        if not self.process or self.process.poll() is not None:
            return
        self._request_id += 1
        envelope = {"jsonrpc": "2.0", "id": self._request_id, **message}
        line = json.dumps(envelope) + "\n"
        try:
            self.process.stdin.write(line)
            self.process.stdin.flush()
        except (BrokenPipeError, OSError):
            pass

    def _recv(self) -> dict | None:
        """
        从MCP服务器接收消息
        
        返回：
        - 接收到的消息
        """
        if not self.process or self.process.poll() is not None:
            return None
        try:
            line = self.process.stdout.readline()
            if line:
                return json.loads(line)
        except (json.JSONDecodeError, OSError):
            pass
        return None


class PluginLoader:
    """
    插件加载器类，用于加载和管理插件
    
    核心功能：
    - 扫描目录中的插件
    - 加载插件清单
    - 提取MCP服务器配置
    """

    def __init__(self, search_dirs: list = None):
        """
        初始化插件加载器
        
        参数：
        - search_dirs: 搜索插件的目录列表（可选）
        """
        self.search_dirs = search_dirs or [Path.cwd()]
        self.plugins = {}  # name -> manifest

    def scan(self) -> list:
        """
        扫描目录中的插件
        
        返回：
        - 找到的插件名称列表
        """
        found = []
        for search_dir in self.search_dirs:
            plugin_dir = Path(search_dir) / ".claude-plugin"
            manifest_path = plugin_dir / "plugin.json"
            if manifest_path.exists():
                try:
                    manifest = json.loads(manifest_path.read_text())
                    name = manifest.get("name", plugin_dir.parent.name)
                    self.plugins[name] = manifest
                    found.append(name)
                except (json.JSONDecodeError, OSError) as e:
                    print(f"[Plugin] Failed to load {manifest_path}: {e}")
        return found

    def get_mcp_servers(self) -> dict:
        """
        提取插件中的MCP服务器配置
        
        返回：
        - MCP服务器配置字典，格式为{server_name: {command, args, env}}
        """
        servers = {}
        for plugin_name, manifest in self.plugins.items():
            for server_name, config in manifest.get("mcpServers", {}).items():
                servers[f"{plugin_name}__{server_name}"] = config
        return servers


class MCPToolRouter:
    """
    MCP工具路由器类，用于将工具调用路由到正确的MCP服务器
    
    核心功能：
    - 注册MCP客户端
    - 识别MCP工具
    - 将工具调用路由到正确的服务器
    - 收集所有MCP服务器提供的工具
    - 断开所有MCP客户端连接
    """

    def __init__(self):
        """
        初始化MCP工具路由器
        """
        self.clients = {}  # server_name -> MCPClient

    def register_client(self, client: MCPClient):
        """
        注册MCP客户端
        
        参数：
        - client: MCP客户端实例
        """
        self.clients[client.server_name] = client

    def is_mcp_tool(self, tool_name: str) -> bool:
        """
        检查是否为MCP工具
        
        参数：
        - tool_name: 工具名称
        
        返回：
        - 是否为MCP工具
        """
        return tool_name.startswith("mcp__")

    def call(self, tool_name: str, arguments: dict) -> str:
        """
        将MCP工具调用路由到正确的服务器
        
        参数：
        - tool_name: 工具名称
        - arguments: 工具参数
        
        返回：
        - 工具执行结果
        """
        parts = tool_name.split("__", 2)
        if len(parts) != 3:
            return f"Error: Invalid MCP tool name: {tool_name}"
        _, server_name, actual_tool = parts
        client = self.clients.get(server_name)
        if not client:
            return f"Error: MCP server not found: {server_name}"
        return client.call_tool(actual_tool, arguments)

    def get_all_tools(self) -> list:
        """
        收集所有连接的MCP服务器提供的工具
        
        返回：
        - 工具列表
        """
        tools = []
        for client in self.clients.values():
            tools.extend(client.get_agent_tools())
        return tools

    def disconnect_all(self):
        """
        断开所有MCP客户端连接
        """
        for client in self.clients.values():
            client.disconnect()


def normalize_tool_result(tool_name: str, output: str, intent: dict | None = None, permission_gate=None) -> str:
    """
    标准化工具执行结果
    
    参数：
    - tool_name: 工具名称
    - output: 工具执行输出
    - intent: 工具意图（可选）
    - permission_gate: 权限门实例（可选）
    
    返回：
    - 标准化的工具执行结果
    """
    if not permission_gate:
        permission_gate = CapabilityPermissionGate()
    intent = intent or permission_gate.normalize(tool_name, {})
    status = "error" if "Error:" in output or "MCP Error:" in output else "ok"
    payload = {
        "source": intent["source"],
        "server": intent.get("server"),
        "tool": intent["tool"],
        "risk": intent["risk"],
        "status": status,
        "preview": output[:500],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


def get_mcp_tools(mcp_router):
    """
    获取MCP相关工具
    
    参数：
    - mcp_router: MCP工具路由器实例
    
    返回：
    - MCP工具列表
    """
    return mcp_router.get_all_tools()
