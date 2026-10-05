# Karpathy 的 autoresearch 循环

Agent 每一轮只做一件事：改一处训练代码，跑 5 分钟，看 val_bpb 有没有降。val_bpb 是模型在验证集上每个字节平均要用的比特数，越低越好。降了就留下这次改动，没降就退回去，然后接着下一轮。

<!-- section:loop -->

```mermaid
flowchart TB
  subgraph grp_files["三个文件，分工不同"]
    direction TB
    human["<small>人来写</small><br/><code><b>program.md</b></code><br/><i>目标、规则和循环步骤</i>"]
    editable["<small>Agent 来改</small><br/><code><b>train.py</b></code><br/><i>模型、优化器、训练过程</i>"]
    locked["<small>谁都不许动</small><br/><code><b>prepare.py</b></code><br/><i>数据、分词器和评测</i>"]
  end
  subgraph grp_actor["做实验的 Agent"]
    agent["<b>Agent</b><br/><i>Claude、Codex 等</i>"]
  end
  subgraph grp_loop["一轮实验"]
    direction TB
    step1["<b>试一个想法</b><br/><i>改 train.py</i>"]
    step2["<b>提交</b><br/><code>git commit</code>"]
    step3["<b>训练 5 分钟</b><br/><code>uv run train.py &gt; run.log</code>"]
    step4["<b>读分数</b><br/><code>grep val_bpb run.log</code>"]
    decide{"val_bpb 降了吗？"}
    keep["<b>保留</b><br/><i>分支往前走一步</i>"]
    reset["<b>git reset</b><br/><i>退回上一个好的提交</i>"]
    log["<b>记一行</b><br/><code>results.tsv</code>"]
  end
  subgraph grp_error["训练崩了怎么办"]
    crash["<b>看日志末尾</b><br/><code>tail run.log</code><br/><i>能修就修，修不了记作 crash</i>"]
  end

  human ~~~ editable
  editable ~~~ locked
  agent -.->|只改它| editable
  agent --> step1
  step1 --> step2
  step2 --> step3
  step3 --> step4
  step4 --> decide
  decide -->|降了| keep
  decide -->|没降| reset
  keep --> log
  reset --> log
  log -->|一直循环| step1
  step4 -.-> crash

  classDef human fill:#FEE2E2,stroke:#DC2626,color:#991B1B
  classDef editable fill:#F3F4F6,stroke:#9CA3AF,color:#374151
  classDef locked fill:#FFFFFF,stroke:#111111,color:#111111
  classDef agent fill:#111111,stroke:#111111,color:#FFFFFF
  classDef keep fill:#DCFCE7,stroke:#16A34A,color:#166534
  classDef reset fill:#FEE2E2,stroke:#DC2626,color:#991B1B
  classDef dashed fill:#FFFFFF,stroke:#111111,color:#111111,stroke-dasharray:5 4
  classDef caption fill:transparent,stroke:transparent,color:#333333
  classDef plain fill:#FFFFFF,stroke:#111111,color:#111111

  class human human
  class editable editable
  class locked locked
  class agent agent
  class keep keep
  class reset reset
  class crash dashed
  class step1,step2,step3,step4,log,decide plain
```

## Agent 要守的规矩

| 规矩 | 具体怎么做 |
| --- | --- |
| 只改一个文件 | 只动 train.py。模型结构、优化器、batch 大小，都在这一个文件里改 |
| 时间固定 | 每次训练跑 5 分钟，一小时大约 12 次；超过 10 分钟的直接杀掉 |
| 裁判不能碰 | prepare.py 里的评测代码不许改，也不许装新的包 |
| 越简单越好 | 分数只好一点点，却多了 20 行别扭的代码，就不要；分数不变、代码变少，就留下 |
| 不停下来问 | 从不问人「要不要继续」，一直跑到有人叫停 |
