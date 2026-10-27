# OCI Always Free 抢机（GitHub Actions 版）

在 GitHub Actions 上 24 小时不停重试创建 Oracle Always Free 实例，抢到后自动停用自己，没抢到自动接力下一个 job，空窗几乎为零。

## 文件说明

| 文件 | 作用 |
| --- | --- |
| `oci_common.py` | 公共逻辑：认证、建/复用 VCN 与子网、端口放行、取镜像、重试循环 |
| `oci_retry.py` | ARM 版，目标 `VM.Standard.A1.Flex`，规格阶梯 `2C/12G → 1C/6G`，100 GB 盘 |
| `oci_retry_micro.py` | x86 版，目标 `VM.Standard.E2.1.Micro`，50 GB 盘 |
| `.github/workflows/oci_retry.yml` | ARM 的接力 workflow |
| `.github/workflows/oci_retry_micro.yml` | Micro 的接力 workflow |

## 一次性配置：6 个 Repository Secret

仓库 → Settings → Secrets and variables → Actions → New repository secret，逐个新建：

| Secret 名 | 填什么 |
| --- | --- |
| `OCI_USER` | 你的 user OCID（`ocid1.user.oc1..…`） |
| `OCI_FINGERPRINT` | API 密钥指纹，形如 `ae:16:7e:…` |
| `OCI_TENANCY` | 你的租户 OCID（`ocid1.tenancy.oc1..…`），它同时被当作 compartment 使用 |
| `OCI_REGION` | 例如 `ap-tokyo-1`（要 identifier 形式，不是显示名） |
| `OCI_PRIVATE_KEY` | `.pem` 文件全文，含首尾 `-----BEGIN/END PRIVATE KEY-----` 两行 |
| `GH_PAT` | GitHub classic PAT，需要 `repo` + `workflow` 两个 scope |

这些值在 OCI 控制台「我的概要文件 → 令牌和密钥 → 该 API 密钥最右侧 ⋮ → 查看配置档案」里能一次拿全 4 项；指纹也可以自己用 openssl 算：

```bash
openssl pkey -in your-key.pem -pubout -outform DER | openssl md5 -c
```

## 推到 GitHub

```bash
git clone https://github.com/<你的账号>/<仓库名>.git
cd <仓库名>
# 把本目录下所有文件（含 .github/）复制进来
git add -A
git commit -m "init: OCI always free retry"
git branch -M main
git push -u origin main
```

**默认分支必须是 main**（接力用的是 `github.ref_name`，如果你在别的分支上触发，它也会回到那个分支，所以其实都行；但第一次请保证代码在 main 上）。

仓库必须是 **public**：私有仓库免费额度只有 2000 Actions 分钟/月，这条链路一个月要烧四万多分钟。

## 启动

1. 仓库 → Actions 页签
2. 左侧选 `OCI ARM Retry`，若显示停用则点 **Enable workflow**
3. 点 **Run workflow** 触发第一次
4. Micro 的 workflow 同理，两个可以同时跑，互不干扰

每个 job 跑 330 分钟后被 `timeout` 杀掉，退出码 124，然后自动 dispatch 下一个 job 接着跑。一条链只会有一个 job 在跑、一个在排队，不会堆积。

## 三种退出码的含义

| 退出码 | 含义 | workflow 行为 |
| --- | --- | --- |
| `0` | 抢到了 | 自动停用本 workflow |
| `124` | 330 分钟到点，还没抢到 | 立刻 dispatch 下一个 job 接力 |
| 其它 | 致命错误（认证失败、配额超限、参数错误） | 打印红色错误并**停链**，等你处理 |

## 失败时的自查顺序

| 日志里的现象 | 原因 | 处理 |
| --- | --- | --- |
| `Out of host capacity` | 该区域这台 shape 真的没容量，正常现象 | 挂着等，只能靠时间磨 |
| `NotAuthenticated` / `401` | 指纹和私钥不匹配，或私钥粘贴时首尾行缺了 | 重新核对 `OCI_FINGERPRINT` 与 `OCI_PRIVATE_KEY` |
| `NotAuthorized` / `403` | 这个 user 没有在该 compartment 建资源的权限 | 换成租户管理员用户，或补 IAM 策略 |
| `LimitExceeded` / `QuotaExceeded` | Always Free 配额已用满（比如已有实例占着） | 去 OCI 控制台确认现有实例，删掉不用的 |
| 一直跑但每次都是 `InvalidParameter` | `OCI_TENANCY` 填成了别的租户，或 `OCI_REGION` 与该租户不符 | 重新从「查看配置档案」核对 |
| 抢到后没停用 / 没接力 | `GH_PAT` 无效或 scope 不够 | PAT 需要 `repo` + `workflow` |

## 抢到之后

日志末尾会直接打印公网 IP 和 ssh 命令。你也可以在 OCI 控制台 → Compute → 实例 里看到。

端口默认放行 `22, 80, 443, 8880`（`8880` 是给 sing-box 之类用的）。要改端口不用改代码，加一个仓库环境变量或在 workflow 里加 `env` 即可：

`OCI_INGRESS_PORTS: "22,443,8080,8880"`

其它可调的环境变量：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `OCI_INGRESS_PORTS` | `22,80,443,8880` | 入站放行的 TCP 端口，逗号分隔 |
| `RETRY_INTERVAL` | `60` | 重试间隔（秒） |
| `UBUNTU_VERSION` | `24.04` | 想要的 Ubuntu 版本，找不到会回退 |
| `OCI_VCN_NAME` / `OCI_SUBNET_NAME` | `retry-vcn` / `retry-subnet` | 复用已有网络时改名即可 |
| `OCI_SSH_PUBLIC_KEY` | 脚本内置 | 覆盖内置的 SSH 公钥 |

## 想改规格 / 名字

- 只想要 `2C/12G` 不想要降级：把 `oci_retry.py` 里的 `SPECS = [(2, 12), (1, 6)]` 改成 `SPECS = [(2, 12)]`
- 改实例名：`INSTANCE_NAME`
- 抢第二台 Micro：把 `oci_retry_micro.py` 的 `INSTANCE_NAME` 改成 `micro-2`，再手动 Run 一次 workflow（抢到第一台后 workflow 会自己停用，需要先 Enable）

## 注意

- 抢到后 workflow 会自己变成 Disabled，这是设计如此，不是出错。
- `GH_PAT` 失效（到期/被撤销）时链路会停，日志里会是红色错误。建议 PAT 设成 No expiration，或定期看一眼 Actions。
- 这个仓库是 public，所以别把 `.pem` 提交进来（`.gitignore` 已挡）；代码里的 SSH 公钥属于公开信息，无所谓。
- 脚本会创建名为 `retry-vcn` / `retry-subnet` 的网络资源，不想要就自己在 OCI 控制台删。
