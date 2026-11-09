# Jarvis Ubuntu Ops 网络扩展

1.4.1 为镜像构建提供固定状态目录下的私有 Docker/Buildx 配置，兼容只读 home 隔离；保留失败阶段及脱敏错误分类。CI 在同样的只读 home、严格文件系统及能力边界下实际构建派生镜像。

1.4.0 增加固定 Codex 客户端修复组件：只从当前镜像派生、校验两个生产源码哈希、保留 CLI 版本；环境和挂载必须与 Compose 一致。应用前建立独立 180 秒旧镜像恢复任务，确认时验证源码、容器健康及同适配器生成/恢复。宿主仓库、.env 与代理配置不写入。详见 [CODEX_CLIENT_REPAIR.md](CODEX_CLIENT_REPAIR.md)。

1.3.1 为未刷新工具清单的客户端保留显式兼容选项：`ops_extension_status(include_runtime=true)` 读取同一固定诊断；`model_smoke_test(mode="harness")` 执行同一适配器测试。默认调用行为保持，选项不接受脚本、路径或提示词。

1.3.0 增加固定 Codex 诊断入口：只读生产后端元数据、源码哈希、进程状态和失败记录；另在独立临时状态中用已安装适配器验证新建、生成与重启后恢复，记录 RPC 阶段耗时。测试不触碰生产适配器或钉钉会话，不投射业务工具，使用只读 sandbox、禁止审批授权；不以该测试替代钉钉验收。

1.2.0 将主备顺序明确为“首选分组、另一分组、其余原候选”，避免混合自动分组插在主备之间；新增 `switch_backup` 计划，只需新鲜备用回执即可恢复备用优先。切换计划支持缩进省略的 YAML 列表与别名，必要时规范化布局并逐值核对变更范围。

1.1.5 在同一受控 DNS 计划中，核对固定隧道服务的非 root 专用账号与真实进程 UID，将该 UID 的流量优先留在一元备用组，并启用严格进程匹配。Jarvis 原业务规则保持；计划展示规则，回滚恢复原规则、DNS 和选择。该规则不授予权限，也不接受调用方 UID、规则或策略名。

1.1.4 的专用节点 DNS 修复计划会同时将梯子猫候选固定到组内已有的日本中转叶子，避免原自动选择链在确认期间漂移。计划公开目标选择；自动主备关系不变，独立回滚同时恢复 DNS 与原选择。

1.1.1 的 `stabilize_dns` 会为已配置的 Laddercat 订阅读取并严格校验其专用节点
DNS 模板，仅给三个固定节点域名增加解析策略，原有全局 bootstrap 与其他域名策略
保持。专用 DNS 明确经独立备用组连接，避免依赖待恢复的主节点；这是恢复阶段的
DNS 引导依赖，不代表业务流量已切主。计划会显示范围，应用前仍有配置校验、
120 秒独立回滚与确认步骤；热重载后清 DNS 缓存，不重启代理进程。更换上游模板、
不可信 URL 或备用不可用时拒绝出计划，不能把订阅里的任意全局配置直接覆盖现网。

## 1.1 执行隔离与旧版迁移

当隧道具有文件系统或地址族隔离时，sudo 改变 UID
不能移除继承的限制。因此 root helper 现在只接受固定动作与
JSON 参数，并通过 PID 1 创建独立 **service**（不是继承调用环境的 scope）。
不修改隧道现有 unit、drop-in 或旧工具的 sudo 规则。

- 普通 worker：`NoNewPrivileges=yes`、`ProtectSystem=strict`、私有临时目录，
  写目录仅网络状态、Mihomo 与 WireGuard 配置；地址族包含 AF_NETLINK。
  仅保留 CHOWN、DAC_OVERRIDE、FOWNER、NET_ADMIN 四项能力。
- 升级 worker：独立异步任务，另允许固定安装目录、固定 helper/sudoers 目录与
  安装事务目录写入。验证账号由 PID 1 创建单独进程，worker 不保留 SETUID/SETGID。
  不等待自身隧道重启；返回 `queued` 后必须查安装与升级回执，不能当作部署成功。
- 版本仍是三个固定文件、固定 GitHub 仓库/current main 与包哈希校验。
  helper 不接受命令、服务名、路径、URL、systemd 属性或 shell。

从 1.0.1 迁移时，旧入口可能已把已校验 payload 写入 `/opt`，随后因 `/usr`
只读而退出。180 秒独立恢复仍有效。新代码兼容旧 wrapper 的固定调用格式；在
该保护窗口内，同一已审查发布的升级请求可以由新 broker 排入独立任务。
安装器仅恢复 **同 commit、同包哈希、175 秒以内且全部 staged 文件仍为 root
所有、不可组/其他用户写、内容与发布逐字一致** 的旧事务，再从原基线重新安装。
不同发布、过期或漂移都拒绝恢复并交给原 guard；不会直接把半安装标为成功。
回滚与验证保存的是正在执行的安装器源码，恢复旧磁盘文件不会替换其后续逻辑。

CI 在一次性 VM 上以 nobody 复现只读系统目录和禁用 NETLINK 的调用环境，检查
sudo 固定入口、worker 的 NETLINK、只允许目标目录写入、异步任务在调用单元退出
后存活，以及升级环境中的独立账号验证和文件属主保留。验证账号由 PID 1 设置，
不依赖 worker 内部的 UID 切换或 PAM 登录会话。该集成测试不可在生产机执行。

在原来的 Ops MCP 服务上增加 11 个专用工具。保留原有工具、认证、隧道和
Jarvis 容器边界，不开放任意 Shell、任意 URL、原始 YAML 写入或任意文件路径。

## 能力与边界

| 工具 | 行为 |
| --- | --- |
| `ops_extension_status` | 版本、初始安装事务状态与发布身份 |
| `network_snapshot` | 脱敏分组、DNS、WireGuard AllowedIPs、未完成事务 |
| `probe_proxy_nodes` | 从服务器对指定机场叶子发起本次新探测，分页返回回执 |
| `prepare_network_change` | 生成可审阅计划，支持主线路切换、DNS 稳定化、WG 运行态持久化 |
| `apply_network_change` | 启用独立 systemd 回滚后应用计划 |
| `confirm_network_change` | 验证通过才取消回滚；不能强制声明成功 |
| `network_change_status` | 查询准确事务状态；超时后先查状态再决定是否重试 |
| `rollback_network_change` | 幂等恢复尚未确认的事务 |
| `read_application_logs` | 读取容器内真正的 app.err.log/app.out.log 诊断行并脱敏 |
| `model_smoke_test` | 现有 ChatGPT OAuth、固定模型和固定提示词的独立测试 |
| `upgrade_ops_extension` | 从本仓库已合并 main 的精确提交与包哈希更新扩展 |

这版**不会创建新的 WireGuard 隧道、修改内核路由或修改节点认证/SNI/证书设置**。
`persist_wireguard` 只把现有单 peer 的公网 `/32`/`/128` 主机路由范围写入
wg0.conf，不扩展到默认路由，也不删除原有 AllowedIPs。更复杂的路由修复需要
通过已安装的受控更新接口增加专用操作，不能借当前接口执行任意命令。

1.0.2 快照保留分组与路由证据，即使 Ops 进程缺少读取 WireGuard 的能力，
也不会整份失败。返回固定入口路由、当前进程的 NET_ADMIN 能力位和受限缓存节点
入口信息，不返回密码、UUID、订阅链接或原始命令输出。节点探测保留控制口 HTTP
状态码，并排除明确的流量/到期提示条目；这些信息用于定位，不能单独归因网络封禁。

## 安装前提

- 已有 `/opt/jarvis-ops-mcp/server.py`、对应 `.venv/bin/python` 和
  `jarvis-ops-tunnel.service`。支持单个顶层官方 SDK 实例及标准 main guard：
  MCP v1 的 `from mcp.server.fastmcp import FastMCP`，或现网使用的 MCP v2
  `from mcp.server import MCPServer`；同样支持这两种导入的别名。
  不认识的服务结构会在写入前停止，不会整份替换未知服务，也不更换现有 SDK。
- Ops 子进程正在运行。安装器从该实际进程的 UID 确定执行身份，而不是猜测
  隧道服务的 User。先调用一次原有 `jarvis_status` 可唤起其 MCP 子进程。
- 宿主机 Python 已安装 PyYAML，且有 systemd、sudo/visudo、Docker、Mihomo。
- 控制口为本机 9090、HTTP 代理为本机 7890，现有分组和 provider 名称见 engine 常量。
- 首次安装只需要一次管理员命令入口。现有 11 个固定 Ops 工具没有自安装入口，
  因而不能由它们自行获得新增的宿主机权限。安装后常规操作和后续发布走 Ops。

## 发布与一次性接入

1. 修改后执行 `python3 ops/build_release.py`，提交 `ops/release.json`。
2. 通过测试及 PR 审查，合并 main。安装器只接受当前 main 精确提交。
3. 从该提交获取 `ops/install.py`，对照 release.json 中的 installer SHA-256
   验证下载文件后，以 root 执行：

   ```sh
   sudo python3 -I /absolute/path/install.py \
     --commit REVIEWED_MAIN_COMMIT_SHA \
     --package-sha256 REVIEWED_PACKAGE_SHA256
   ```

   安装器从固定仓库读取其余三个文件，校验提交和每个文件哈希后安装到
   `/opt/jarvis-ops-network`，配置专用 root helper 的 sudo 白名单，并在原 MCP
   main guard 之前注册工具。以实际服务账号调用 SDK 公共 `list_tools()` 接口，
   同时检查全部原有工具与全部 11 个扩展工具。不会清理或拉取服务器上有未提交
   改动的 Jarvis 仓库。

4. 初始安装先备份并启动 180 秒独立恢复任务，检查 MCP 注册后延迟重启 **Ops 隧道**，
   不重启 Jarvis、Mihomo 或 WireGuard。随后以实际服务账号验证 sudo bridge、
   配置读取和控制口访问，失败自动还原。成功将安装事务标为 committed。
5. 刷新客户端的 MCP 工具发现，再调用 `ops_extension_status`，确认新版本和
   安装事务 committed；随后实际调用 snapshot、probe 和 model_smoke。
   “服务 active”或本地单测通过不能替代这一步。

后续升级：在 GitHub 审查、合并发布，再调用
`upgrade_ops_extension(commit_sha, package_sha256)`。更新只允许固定仓库的当前
main、固定三个文件和匹配的包哈希；延迟重载保证先返回升级回执。

## 主线路恢复流程

1. `network_snapshot()` 核对当前主备关系，不先动现有流量。
2. `probe_proxy_nodes(provider="primary", offset=0, limit=4)`：并发最多 2，分页最多
   6 节点，超时最多 8000ms；没有沿用历史 alive。分别检查 gstatic HTTP 204 和
   OpenAI 未认证 HTTP 401，**后者只证明传输可达，不证明模型生成**。
3. 同样探测 backup。选择主备均成功且不超过 180 秒的回执，调用
   `prepare_network_change(profile="switch_primary", ...)`。
4. 审阅计划后应用：先确认独立 120 秒恢复 timer active，再写配置并切选择器；
   主线路优先，一元备用直接留在两个顶层 fallback 组中，避免备用只藏在主组内。
5. 通过实际传输、DNS 和两个顶层组均已选主线路的验证后才能 confirm。
   若 `primary_not_selected_yet`，保持原截止时间，等待健康检查更新后再核验；
   不延长回滚窗口。任何未确认变更到期会独立恢复，即使 MCP 已断连。
6. 运行 `model_smoke_test()` 再在钉钉验收。模型测试固定为 `gpt-6.1-sol / high`，
   read-only sandbox、独立 ephemeral 线程，内层 90 秒强制超时，不用 API Key。

已 committed 的变更不会被迟到回滚任务覆盖。需要撤销已确认状态时，应基于
当前状态重新生成反向计划，避免用旧快照覆盖新改动。人工同时改同一配置会触发
drift 错误并保留证据；不能悄悄覆盖另一位操作者的文件。

## 验证

```sh
python3 ops/build_release.py --check
python3 -m unittest discover -s tests -p test_ops_network.py -v
python3 -m unittest discover -s tests
```

测试覆盖新探测回执、过期/失败回执、独立恢复顺序、确认失败、延迟回滚、配置
漂移、路由范围限制、发布哈希、安装失败恢复、服务账号权限验证及真实 MCP SDK
注册及调用。CI 分别安装真实 MCP 1.29.0、2.0.0、2.3.0；v2 使用含全部 11 个
现网工具、权限标注和 Annotated 输入的无秘密结构样本，验证扩展后共 22 个工具。
权限标注兼容两代 SDK 的字段名。sudo helper 使用 Python `-I` 隔离环境，返回固定错误码，密钥仅在宿主机
私有文件中使用；备份与事务目录权限为 0700、数据文件 0600。
