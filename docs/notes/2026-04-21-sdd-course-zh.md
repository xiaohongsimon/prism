# Spec-Driven Development with Coding Agents — 中文字幕整理

> DeepLearning.AI × JetBrains 短课，讲师 Paul Everitt。
> 共 14 节视频（L0–L13，约 9000 词），本文件为双语整理：每节先英文原文分段，后中文翻译分段，不做删减。

---

## L0 · Introduction（引言）

### English

Welcome to this course on Spec-Driven Development built in partnership with JetBrains. Spec-Driven Development is currently the best type of workflow for building serious applications with agentic coding assistance. Give your coding agent a markdown file or a long prompt, explaining exactly what to build and it implements that spec. Rather than writing code by hand, you focus on writing down the context that the agent doesn't already have.

I'm delighted that our instructor for this course is Paul Everett, who's developer advocate at JetBrains. — Thank you Andrew, and wait, I didn't know you wore spectacles. — You're right. I don't actually need these. — Okay, that's what I thought.

Anyway, Spec-Driven Development has three main benefits that you'll start to see right away. First, you can control large code changes with small changes to the spec. One sentence like "use SQLite with Prisma ORM" might affect hundreds of lines of code. Change that to MongoDB for the same downstream amplification. This makes writing specs really efficient, far more so than writing code. Second, specs help eliminate context decay between sessions, preserving the non-negotiable. Agents are stateless, so loading them with the highest quality context right when they boot up is important. And finally, specs improve your intent fidelity. You define the problem, success criteria, constraints, and so on, and the agent can elaborate to create a fuller plan.

One way I often write a spec is by having a conversation with an agent like Claude Code or Gemini or ChatGPT Codex, to make the key architectural choices using my knowledge of how I want to make different trade-offs. Then have the agent summarize the key decisions in a markdown file. Writing a spec requires thinking, and this is hard work. You have to decide what product you want to build, what are its features, its technical architecture. And without a spec, you'll be leaving these important decisions up to the whims of the coding agent, which might be okay if you want to move really fast and just roll the dice, but certainly leads to less maintainable code and sometimes pretty weird products. For example, I've seen teams working on a complex software product where there was no clear spec, and this led to many downstream headaches from these different coding agents under the direction of different developers, building quickly, building in contradictory ways.

Spec-Driven Development involves developing a constitution at the project level to define the immutable standards. Then iterating through feature development loops. These loops isolate each feature on its own branch with plan, implement, and verify steps that leave a clean slate between features and reduce headaches and context switching. This same workflow supports both greenfield and brownfield projects. In greenfield projects, you start from scratch. You'll develop the constitution in a conversation with the agent. In brownfield, existing code bases, you'll generate the project constitution based on the existing code base. In both cases, you'll then iterate through these feature development loops, managing versioning in small steps. In this course, you'll also see how to write your own agent skills to automate your spec-driven workflow.

You know, if you can accomplish what you need in just one short prompt, that's great. I'm definitely an advocate of lazy prompting when it works. But the great developers I know out there almost always will write detailed specs for projects with any significant complexity, because they have unique context and an opinion on what or how to build that'll be superior to letting the LLM, which is missing that context, pick randomly. If a coding agent is going to go off and write code for 20 or 30 minutes, which may correspond to several hours of traditional developer work, you're often better off sitting down for three or four minutes and writing really clear instructions.

Many people have contributed to this course, including Konstantin Chaika and Zina Smirnova from JetBrains and Isabel Zaro from DeepLearning.AI. Let's go on to the next video and let's write some specs.

### 中文

欢迎来到这门 Spec-Driven Development（规范驱动开发）课程，由我们与 JetBrains 联合打造。在当下用 agentic coding（智能体编程助手）构建正经应用的所有工作流里，Spec-Driven Development 是最好的一种。你给 coding agent 一个 markdown 文件或一段长 prompt，清楚描述要构建什么，它就照着这份 spec 去实现。你不再手写代码，而是专注于写下 agent 还不知道的那些上下文。

我很高兴这门课的讲师是 Paul Everett——他是 JetBrains 的 developer advocate。——谢谢你，Andrew。等一下，我没想到你戴眼镜了？——你说得对，其实我根本不需要这副眼镜。——好吧，我就知道。

言归正传。Spec-Driven Development 有三大核心收益，你一上手就能感受到。第一，你可以用很小的 spec 改动去控制很大的代码改动。一句话——比如"use SQLite with Prisma ORM"——可能会影响几百行代码；把它改成 MongoDB，下游也会同样被放大。这让写 spec 变得极其高效，远比直接写代码划算。第二，spec 能消除会话之间的"上下文衰减"，保住那些不可妥协的东西。Agent 是无状态的，所以它启动的那一刻就给它灌最高质量的上下文非常关键。第三，spec 能提升你的 intent fidelity（意图保真度）——你把问题、成功标准、约束条件这些都定义好，agent 就能在此基础上展开出更完整的计划。

我自己常用的一种写 spec 方式是：和 Claude Code、Gemini 或 ChatGPT Codex 这样的 agent 聊，用我对各种权衡的判断去敲定关键的架构选择，然后让 agent 把关键决策总结成一份 markdown。写 spec 需要思考，这是件硬活儿。你必须决定要做什么产品、它有哪些功能、技术架构是什么。没有 spec，这些重要决策就会被随手扔给 coding agent 的偶然性——要是你就想飞快推进、掷骰子碰运气，那也许没关系，但几乎必然会导致代码不可维护，有时还会做出很奇怪的产品。举个例子，我见过团队在做一个复杂软件产品，没有明确 spec，结果不同开发者指挥着不同 coding agent 高速、但方向互相矛盾地建设，下游生出一堆头疼事。

Spec-Driven Development 的做法是：在项目层建立一份 constitution（章程），定义那些不可变的标准；然后迭代地走一轮轮"特性开发循环"。每个特性在自己的 branch 上被隔离，经历 plan、implement、verify 三步，特性之间留下干净的边界，减少头疼和上下文切换。这一套工作流对 greenfield（全新项目）和 brownfield（已有代码库项目）都适用。Greenfield 项目你从零开始，通过和 agent 对话来写 constitution；Brownfield 项目则基于已有代码库生成项目 constitution。两种情况下，接下来都是在小步迭代中走这些特性开发循环，管理版本。在本课程中，你还会学到如何编写自己的 agent skill，把你的 spec-driven 工作流自动化。

你懂的，如果一个短短的 prompt 就能解决问题，那很好——能"懒 prompt"就懒 prompt，我是坚定的支持者。但我认识的那些厉害的开发者，只要项目有一点点复杂，几乎都会写详细的 spec；因为他们拥有独有的上下文和判断，知道该做什么、怎么做，这比让缺失这些上下文的 LLM 随手一抓要强得多。如果 coding agent 要独自跑 20、30 分钟写代码（相当于传统开发好几个小时的工作量），那你先坐下来花三四分钟把指令写清楚，通常是更划算的。

这门课得到了很多人的帮助，包括 JetBrains 的 Konstantin Chaika 与 Zina Smirnova，以及 DeepLearning.AI 的 Isabel Zaro。进入下一个视频，开始写 spec 吧。

---

## L1 · Why spec-driven development?（为什么要用规范驱动开发）

### English

When you hear "agentic coding", you might think Vibe coding. Let's compare the two and see how Spec-Driven Development gives better results by bringing back engineering. Vibe coding gives quick results. You write a prompt describing what you want, like "create me a button" and hope for the best. Then you look at the result. That's a big button. It's kind of close, but off on some important things. So you point out the mistakes to the agent, it tries again and so on until you are satisfied. As a result, you will end up with a long dialogue with the agent, the history of which will not even be saved.

This approach works okay for a button, but it doesn't scale to a large ongoing project. While high-level prompts are fast, they lead to disposable code and mounting technical debt. We need engineering. A well-maintained specification that creates a permanent technical artifact. Spec-Driven Development is the professional response to the chaos of unsupervised AI generation. It is a paradigm shift where the Specification, explaining the what and why, is decoupled from the Implementation, the how. With specs, we get a contract between the humans, but also with the agent. Your main task as the human now shifts. Learn how to convert your intentions into clear specifications.

Spec-driven development with agentic coding assistance has three main benefits. First, you're able to control large code changes with small changes to the spec. A few sentences in the spec outlining the look and feel of the app might translate to hundreds of lines of CSS. This spec-driven approach reduces the cognitive overhead needed for working with these ultra-fast coding agents. Second, specs eliminate the context decay problem that derails multi-turn agent sessions. As you work with your coding agent, its context window will fill up, often leading to more mistakes as the agent tries to cope with a full working memory. Specs persist between sessions and even agents, anchoring the agent to the core context needed to work in a code base and implement a feature. Third, specs improve intent fidelity, meaning that the agent is more likely to produce code that matches your goals. That's because specs force you to define the problem, success criteria, constraints, user flows, and so on before the agent starts generating code.

Specs are a key differentiator between vibe coding — some slop — and engineering a viable software product. Whether you are starting a new project from scratch or want to implement the SDD approach into a project that has been running for years, specs help solve the drift and productivity problems. As a comparison, think of compilers, which convert understandable source code into machine code. SDD guides the agent and prompts, converting specs into source code. Even better, the specs are in a human language, making it easy for stakeholders.

Spec-driven development has taken off recently as a solution to concerns about productivity. Multiple SDD projects, tools built around spec authoring, conference talks on capturing intent. This is all part of a broader push to bring engineering — lessons learned from the software development life cycle — to agentic coding.

Spec-Driven Development is used with Coding Agents, not simple chatbots. A chatbot can talk about code, but the chatbot doesn't have access to your project's code nor tools you have installed. It just responds to your prompts. Agents are different. They take your prompt, make a plan, and guide themselves to a result using reasoning along the way. Importantly, agents have access to your code base and your development tools. In the SDD workflow, we treat these agents as highly capable pair programmers. They provide the technical knowledge and the speed, and you, the senior architect, provide the blueprints. As we move forward in this course, remember: the agent is the muscle, but the SPEC is the brain. With practice, you ensure that the software produced is not just functional, but is aligned with your long-term goals.

### 中文

一提 agentic coding，你可能会想到 Vibe coding。我们把这两者对比一下，看看 Spec-Driven Development 是怎么靠"把工程学带回来"取得更好结果的。Vibe coding 出结果很快：你写一段 prompt 描述你想要什么，比如"给我做一个按钮"，然后祈祷效果好。看结果：好大一个按钮，有点接近了，但某些重要地方不对。于是你把问题指出来让 agent 再试一次，如此反复直到满意。结果就是你和 agent 留下了一段很长的对话，这段历史甚至不会被保存。

这种方式做个按钮还行，但撑不起一个持续进行的大项目。高抽象度的 prompt 速度快，但产出的是"一次性代码"，技术债越积越多。我们需要工程学：一份被好好维护的 specification，作为一个永久性的技术产出物。Spec-Driven Development 就是对"无人监督 AI 生成"那种混乱局面的专业回应。它是一次范式切换——把说明"做什么、为什么"的 Specification 和说明"怎么做"的 Implementation 解耦。有了 spec，我们既在人与人之间拿到了一份契约，也和 agent 之间拿到了一份契约。作为人类，你的主任务也变了：学会把你的意图转化为清晰的 specification。

Spec-Driven Development 搭配 agentic coding 有三大收益。第一，你能用 spec 的小改动控制代码的大改动——spec 里描述外观和风格的几句话，可能对应几百行 CSS。这种 spec-driven 的方式大幅降低了与这些极速 coding agent 协作时的认知负担。第二，spec 解决了多轮 agent 会话中"上下文衰减"的问题。你和 coding agent 协作时，它的 context window 会被填满，常常在"工作记忆塞满后"开始犯更多错误。spec 在会话之间、甚至不同 agent 之间都能持续存在，把 agent 锚在"在此代码库中工作、实现这个特性"所需的核心上下文上。第三，spec 提升 intent fidelity——让 agent 更可能写出对齐你目标的代码。因为 spec 逼你在 agent 动笔之前就把问题、成功标准、约束、用户流程这些定义清楚。

Spec 是"vibe coding 糊出来的烂泥"和"工程化出来的可行软件产品"之间的关键分水岭。无论你是从零起新项目，还是想把 SDD 引入一个已经跑了几年的项目，spec 都能解决漂移与生产力问题。类比一下：编译器把人类可读的源代码编译成机器码；SDD 则引导 agent 和 prompt，把 spec "编译"成源代码。更妙的是，spec 是人类语言写的，方便各路利益相关方参与。

Spec-driven development 最近突然火起来，就是因为它回应了大家对生产力的焦虑。多种 SDD 项目、围绕 spec 创作的工具、关于"捕捉意图"的大会演讲——这些都是一股更大浪潮的一部分：把工程学、把软件开发生命周期里学到的教训，带进 agentic coding。

Spec-Driven Development 是和 Coding Agent 配合用的，不是和简单聊天机器人。Chatbot 能聊代码，但它访问不到你项目的代码，也动不了你装的那些工具，它只对 prompt 做出回应。Agent 不一样：它接下你的 prompt，做计划，沿途靠推理指导自己走向结果；更关键的是，agent 有你代码库和开发工具的访问权限。在 SDD 工作流里，我们把这些 agent 当作"能力极强的结对编程搭档"。它们负责提供技术知识和速度，而你作为资深架构师，负责提供蓝图。继续学下去时请记住：agent 是肌肉，SPEC 才是大脑。练得多了，你就能确保产出的软件不仅能跑，还能对齐你的长期目标。

---

## L2 · Workflow overview（工作流总览）

### English

Spec-driven development tells the agent how to build what you want. Up front for the project, then for each feature, getting better as you go. With SDD, we help the agent with the best quality context. First with decisions about the project, then with details about each feature. This spec is very detailed.

One key skill in SDD is knowing the right level of detail. If you treat your agent as a highly capable pair programmer, you'll often hit the right level. Lots of context about the Goals, mission, target audience and constraints. And less about the low-level decisions the agent can figure out on its own.

What does this SDD workflow look like? First, we specify the Constitution. What is the mission, the Tech stack, the Roadmap? A Constitution is just one way to formalize these project level details. Many developers use a top-level agents.md file for this purpose. But a project Constitution is agent-agnostic and more structured. The Constitution captures the agreement on key decisions between the human and the agent, but also the agreement between the humans.

The mission explains the why. This project's vision, audiences, scope, etc. Defining these parameters in advance is very common in software projects and helps guide ongoing decisions. The tech stack is, for the engineering team, a common understanding of development and deployment technologies and constraints. The Roadmap is a living document with a sequence of phases, each implemented with their own feature spec process.

Once the Constitution has been drafted, we work on each feature with a repeatable process. First, plan the feature, implement it, and finally, validate the result. In between features, it's time for the Replanning phase. Revise your Constitution, update the road map, even improve the process itself.

As you heard a moment ago, one of the key skills of SDD is providing the right level of detail to create the highest quality context. In the feature phase and the replanning phases, you steer the agent. Look at it this way. Imagine that you are an architect and you give detailed drawings of a building to builders. Then it's up to them. Your role is to design, supervise the construction, then review and accept the results, or ask for changes. You'll want to avoid telling the builders how to do their jobs and focus on providing the context they don't know.

Spec-Driven Development gets you from thinking at the start to delivering at the finish. Best of all, it keeps you improving from there. This is one SDD workflow that modern developers are starting to use. Soon we'll look at the start, the project constitution. But first, let's do a little setup preparation. To get set up with the project repo, follow instructions in the next reading item. The following video setup is optional. If you're already comfortable with setting up a coding agent like Claude Code in an IDE such as WebStorm, feel free to skip this optional video. See you in the next video or the next one.

### 中文

Spec-driven development 告诉 agent **怎样**去构建你想要的东西：先为项目定调，再为每个特性定调，一路越做越好。SDD 的核心就是给 agent 提供最高质量的上下文——先是项目层面的决策，再是每个特性的细节。这份 spec 会非常详细。

SDD 的一项关键技能是**判断合适的详尽度**。如果你把 agent 当作一位能力极强的结对编程搭档，你通常就能命中合适的层级：对目标、使命、目标受众、约束条件给足上下文；对那些 agent 自己就能琢磨出来的低层决策少说。

那么 SDD 工作流是什么样的？首先，敲定 Constitution（章程）——使命是什么，技术栈是什么，路线图是什么。Constitution 只是把"项目层信息"正式化的一种方式。很多开发者用顶层的 `agents.md` 文件来承担这个角色；而 project Constitution 与 agent 无关，结构更严整。Constitution 承载的是"人与 agent 在关键决策上的共识"，也是"人与人之间的共识"。

使命（mission）解释"为什么"——项目的愿景、受众、范围等。提前定义这些参数在软件项目里非常常见，能指导后续决策。技术栈（tech stack）是工程团队对开发和部署技术与约束的共同理解。路线图（Roadmap）则是一份活文档，是一串阶段序列，每个阶段都会走各自的 feature spec 流程。

Constitution 成稿后，每个特性都走一套可重复的流程：先 plan（规划），然后 implement（实现），最后 validate（验证）。特性之间是 Replanning 阶段——修订 Constitution、更新路线图，甚至改进流程本身。

刚才也提到，SDD 的一项关键技能是用合适的详尽度塑造最高质量的上下文。在 feature 阶段和 replanning 阶段，你都在"驾驶" agent。可以这样想：你是建筑师，把建筑详图交给施工方，剩下的就看他们了。你的角色是设计、监督施工，然后审查验收、必要时提出修改。你要避免告诉施工方"怎么干活"，而是聚焦在"他们不知道的那些上下文"上。

Spec-Driven Development 带你从"开头的思考"一路走到"收尾的交付"；最棒的是，它让你之后还能持续改进。这就是现代开发者开始采用的一套 SDD 工作流。稍后我们会从起点——项目 constitution——开始。但先做点准备：按下一节阅读材料去配置项目 repo。接下来的视频 setup 是可选的，如果你已经熟悉在 WebStorm 这类 IDE 里配置 Claude Code 这样的 coding agent，可以跳过那段。下一节或再下一节见。

---

## L3 · Setup（环境准备）

### English

Before you send your first prompt, let's talk a little bit about setting up your workspace. If you're already comfortable with setting up a coding agent like Claude Code in an IDE such as WebStorm, feel free to skip this optional video.

Spec-Driven Development is a best practice that isn't tied to any specific IDE or coding agent. So, you can choose the setup you're already using or the same one you'll see in this course. VS Code with the Codex CLI, Zed editor with a local model — all great for spec-driven development. Since we are planning to develop a web application, this course will use the WebStorm IDE with Claude Code as a coding agent. Instructions on downloading and installing both have been included in the previous reading item.

Let's open up WebStorm and start a brand new project named Agent Clinic. This will be a TypeScript project with a Git repository to keep close track of versioning your code and specs. Although many IDEs, including WebStorm, offer a chat panel, we know there's a lot of diversity in how you interact with your coding agents.

In spec-driven development with agents, it's important to keep close track of versioning your code. Let's say we want to create an initial commit using the agent. Every time it needs to execute a command, it will ask you for the confirmation unless you start Claude Code in an unsafe mode. Pay close attention to what Claude Code asks you to do. The ultimate responsibility for the code is yours.

Throughout the coming lessons, you'll see several tricks and best practices for spec-driven development with Git. Great, we've got our setup ready and you've got yours ready. Let's get started.

### 中文

发出第一条 prompt 之前，先聊聊工作区怎么配。如果你已经熟悉在 WebStorm 这类 IDE 里配置 Claude Code 这样的 coding agent，这节可选视频可以跳过。

Spec-Driven Development 是一种不绑定任何特定 IDE 或 coding agent 的最佳实践。所以你完全可以沿用你已有的配置，也可以照搬课程里的这套。VS Code + Codex CLI、Zed 编辑器 + 本地模型，都很适合做 spec-driven development。由于我们要做的是一个 Web 应用，本课程会使用 WebStorm IDE + Claude Code 作为 coding agent。这两者的下载安装说明在前面那条阅读材料里。

打开 WebStorm，新建一个名叫 Agent Clinic 的项目。这会是一个 TypeScript 项目，并带一个 Git 仓库，以便密切跟踪你代码和 spec 的版本。尽管包括 WebStorm 在内的很多 IDE 都提供 chat 面板，但我们知道大家与 coding agent 交互的方式差异很大。

在用 agent 做 spec-driven development 时，紧盯代码版本非常重要。比如我们想让 agent 创建一个 initial commit，只要它需要执行命令，默认都会向你确认——除非你用不安全模式启动 Claude Code。仔细看 Claude Code 让你做的每一件事。对代码的最终责任在你自己。

在接下来的课里，你会看到若干 spec-driven development + Git 的小技巧与最佳实践。好，我的环境准备好了，你的也准备好了，开始吧。

---

## L4 · Creating the constitution（编写项目章程）

### English

You need to tell your agent about your project, the mission, audience, and other decisions. In this lesson, we'll work together with the agent to write the Constitution. What really is your project? Say you're working on a new web app for your company. What's the core idea behind its development? How does it fit within your company's preferred tech stack? What features are planned? These three foundational principles form the Constitution, a global set of high-level requirements that will guide future feature development and explain the project's shape to stakeholders.

For example, you have so many choices for your tech stack. You'll want to narrow down your options based on appropriate tradeoffs and what you use at your company. We need to write this down — for the agent, for your teammates, for the future. But we don't write it alone. We write it in a conversation with the agent. You'll be surprised at the great questions it will ask: architecture patterns you hadn't considered, external packages that already do the work, or tradeoffs, for example, speed versus data fidelity.

The mission, tech stack, and roadmap aren't just properties of a Greenfield project you're starting from scratch. Existing codebases use these three pillars too. We will talk about bringing the SDD workflow to existing projects in just a few videos from now. Now, let's talk about completely new projects.

We are writing AgentClinic, a place for AI agents to get relief from their humans. AgentClinic is a fun parody of the popular learning project Pet Clinic. Coding agents are doing a lot of work. It's stressful. Hallucinations, context rot, memory issues and co-worker sub-agent coordination all take a toll on agent health. Let's build a clinic where they can get help with their issues. It's a full stack web app with a Next.js back end and a React front end. The app allows you to manage appointments, ailments like hallucination, and treatments like a context infusion. Let's get started on the development.

Let's take a look at how you might go about writing a detailed spec for a project like this. We're about to look at a lot of text, but this technique will pay off for you down the line. Yes, it's common to have a spec this long. This document contains a lot of information that the average Claude code instance isn't going to know. For example, lines 37 through 43: real problems we're trying to solve. Agents can check themselves in via API and their issues persist over time so we can see how effective treatments are. Line 57: the visit lifecycle is mapped out including a TRIAGE step to route them to the right place, and a FOLLOW-UP with three possible states. Line 379: we also have an ailment catalog plus the ability to have custom ailments. Ailments have codes and severity levels, which can be modified if the agent, for example, handles medical or legal data. Line 430: we'll auto-create a custom ailment when symptoms don't match any existing ailment above a 0.6 similarity threshold. Line 581: we specify the exact algorithm used to update a treatment's effectiveness.

Let's chat with the agent to see if there's anything that could be cleared up. We've already done a few rounds of this, but there will still be some lingering questions and you could pretty much always clarify more. The agent says there's a threshold inconsistency in the diagnosis flow. Let's scroll to line 143 and check it out. It looks like three is actually the correct option. If a diagnosis is between 0.4 and 0.6 confidence, then it's included but flagged as uncertain. So, this is a great sign. There's no issue with the spec. We're just confirming our understanding with the agent.

For this question, let's just say having an unprotected dashboard is fine because we expect to deploy this privately in a secure environment to our company. For the LLM, let's leave that configurable because we don't know how soon a new model will be released. For archiving, we're not sure about this behavior right now, so let's choose the first option, soft-delete, which gives us the most flexibility later.

We'll be moving forward with a somewhat more pared down mission document. In the repo, you'll find both. You can choose to use either version. For a challenge, try the most complex one shown here; to follow the course exactly, use the shorter ones.

Next, let's take a look at the tech-stack file. This document separates out the key architecture decisions from the mission document. The product can be implemented in many ways, but some are better than others. Let's walk through some key ideas to include. Line 51: the full pipeline that happens when an agent hits the /visits route with a post request and involves validating the request, searching for previous visits, and so on. Line 588: the database schema for all the tables. This is great to have in advance because it's such a headache to update the schema later. Line 738: a list of all possible treatments like Temperature Reduction. Then on line 706, we see which ailments get which treatments. Line 899: we go through some basic smoke tests like checking to make sure that chronic ailments are detected.

Again, let's see if anything needs to be cleared up. This is a great question to help us align our mission and tech-stack. We'll select option two so that our environment variable for the LLM matches in both places. This is a debatable point, but let's keep it open for now. If we're assuming the MVP is on a private secured network, we can refactor this later. Let's accept the stale state when the dashboard reconnects after a dropped SSE connection, because a full refresh will pull the fresh state anyway. That's good enough for an MVP. For the prescription payloads, let's add the schema_version now. It's a super lightweight change that makes things a lot easier down the line.

Again, we'll be using a smaller document moving forward. You can choose to use either version to follow along with. To generate the more lightweight documents, let's chat with the agent. First, we provide our project description to the agent and tell it that our stakeholders gave their input in README.md, which we added to the repo. In the important note, we directly mention Claude code's AskUserQuestion tool. This tool is totally optional, but we just like the way it looks in the interface. Also, for the project's constitution, tell the agent to work with you on a mission, tech stack, and roadmap. Spec-driven development works best with a human-in-the-loop approach where you review small changes. So tell the agent to organize the roadmap in small steps.

As we work together, you'll see the agent ask some really good questions. You might see different ones. The first question says, "what tone should the mission.md take?" I'll choose, you guessed it, playful. That covers the mission. Now some questions for the tech stack. Our engineers are used to TypeScript on the back end, so let's add that to the requirements. Next for our roadmap. It asks how granular it should be. I'll choose the first option. We finished the initial interview. Let's choose submit answers.

The agent will ask you for write permissions. This keeps changes under your control. If you are comfortable with the security tradeoffs, you can select the option to approve all instances of a particular command for this session. You'll see this in the rest of the course. Now we're done and we have three files in the specs directory: mission.md, tech-stack.md, roadmap.md.

It's human-in-the-loop time. Let's review. For example, the mission left out a target audience. That's reasonable — how could the agent know your business? Instead of editing directly, let's continue the conversation. To keep all artifacts consistent, it is better practice to ask the agent to make changes to them. Manually, and you might miss updating related documents. We want to use SQLite for this project since this is a quick prototype. We didn't mention it, but the agent figured it out in recommendations. It's now part of our tech stack. Do one final review. It's important to get everything right up front. Let's commit the Constitution so it will be a living document for the project. AgentClinic now has a constitution, mission, roadmap, tech stack, to guide our project. We're ready to tackle our first feature.

### 中文

你得告诉 agent 关于这个项目的事——使命、受众和其他决定。本节我们和 agent 一起写 Constitution。你的项目到底是什么？比如你在给公司做一个新的 Web 应用：它背后的核心想法是什么？怎么嵌入公司偏好的技术栈？打算做哪些功能？这三个基础原则共同构成 Constitution——一套全局的高层需求，既指导未来特性开发，也向利益相关方解释项目形态。

比如说技术栈，你有一大堆选择。你要基于"合适的权衡"和"公司在用的东西"把范围收窄。我们要把这些写下来——为了 agent，为了队友，为了未来。但我们不是一个人写：是在和 agent 对话中写。你会惊讶它能问出多好的问题：你没考虑过的架构模式、已经有人写好的外部包、权衡取舍（比如速度 vs 数据保真度）。

使命、技术栈、路线图这三根柱子不只属于"从零开始的 Greenfield 项目"。已有代码库的项目也要用。几节之后我们会专门讲怎么把 SDD 工作流引入已有项目。现在先讲全新项目。

我们要写的是 AgentClinic——一个让 AI agent 从人类那里来喘口气的地方。AgentClinic 是对那个热门教学项目 Pet Clinic 的调皮致敬。Coding agent 现在干活儿多啊，压力大：幻觉、上下文腐化、记忆问题、和协作的子 agent 之间的协调，都在消耗 agent 的健康。我们就给它们建一家"诊所"让它们看病。这是一个全栈 Web 应用——Next.js 后端 + React 前端，支持管理预约、管理 ailment（比如 hallucination 这样的"病"）、管理 treatment（比如"上下文输液"这样的"治疗"）。开工。

我们来看看给这种项目写一份详细 spec 大概是什么样。接下来会看很多文字，但这份功夫后面会回报你。是的，spec 长成这样很常见。这份文档包含很多信息，是一般的 Claude Code 实例默认不知道的。比如第 37–43 行：我们要解决的真实问题——agent 可以通过 API 自行挂号，它们的问题在时间维度上持久化，这样我们才能衡量治疗的效果。第 57 行：就诊生命周期被画了出来，含一个 TRIAGE（分诊）步骤把 agent 路由到对的地方，还有一个有三种可能状态的 FOLLOW-UP。第 379 行：有 ailment 目录，同时允许自定义 ailment；ailment 有 code 和严重度等级，如果这个 agent 处理的是医疗或法律数据，等级可以调整。第 430 行：当症状和任何已有 ailment 的相似度都不超过 0.6 阈值时，我们自动创建一个 custom ailment。第 581 行：我们给出更新 treatment 有效性的精确算法。

我们和 agent 聊一下，看有没有要澄清的地方。这样的来回我们已经走过好几轮了，但总还有悬着的问题，而且"再澄清一点"几乎永远有价值。Agent 说诊断流程里有个阈值不一致。我们翻到第 143 行看看。看起来选项三才是对的：如果诊断的置信度在 0.4 到 0.6 之间，那就纳入但标记为不确定。这是个好信号——spec 没问题，只是我们和 agent 在确认共同理解。

这个问题我们就回答：没有权限保护的 dashboard 没关系，因为我们预期这玩意儿会私有地部署在公司内部的安全环境里。LLM 那项留可配置，因为不知道下一款新模型什么时候出来。归档行为我们现在还没想清楚，那就选第一个选项——soft-delete（软删除），后面留最大灵活度。

我们会用一份更精简的 mission 文档往下走。repo 里两份都有，你可以任选。想挑战就用最复杂那版；想严格跟课就用短版。

接下来看 tech-stack 文件。这份文档把关键架构决策从 mission 文档里分离出来。一个产品可以有很多种实现，但有些比另一些更好。过几个要点：第 51 行：当 agent 以 POST 请求命中 `/visits` 路由时，完整的处理管线是什么——验证请求、查历史就诊记录等等。第 588 行：所有表的数据库 schema——提前有它太有用了，因为以后改 schema 太头疼。第 738 行：所有可能的 treatment 列表，比如 Temperature Reduction。第 706 行：哪些 ailment 对应哪些 treatment。第 899 行：基本的冒烟测试，比如确保慢性 ailment 能被识别出来。

再问一次是否要澄清。agent 问的这个问题很好，能帮我们把 mission 和 tech-stack 对齐。我们选第二项，这样 LLM 的环境变量在两边保持一致。另一个是可争论点，先不定，如果 MVP 假设在私有安全网络里，以后可以重构。dashboard 在 SSE 连接断开重连后，我们接受先呈现 stale 状态，反正一次全量刷新就会把最新状态拉回来——对 MVP 够用。对于 prescription 的 payload，我们现在就加 `schema_version`——一处非常轻量的改动，未来省大麻烦。

再强调一次，我们会用更短那版往下走。你可以任选一版跟着做。要生成更轻量的那版文档，我们和 agent 聊。首先向 agent 提供项目描述，并告诉它"利益相关方的输入在我们已经加进 repo 的 README.md 里"。在那条"重要提示"里，我们直接点名 Claude Code 的 AskUserQuestion 工具。这个工具完全可选，只是我们喜欢它在界面里展示的样子。另外对于项目 constitution，让 agent 和你一起搞定 mission、tech stack 和 roadmap。Spec-driven development 最适合"human-in-the-loop"（人在回路）——你审每一次小改动。所以告诉 agent 把 roadmap 组织成小步。

一起做的过程中，你会看到 agent 问出一些非常好的问题。你那儿看到的问题可能不一样。第一个问题是："mission.md 该用什么语气？"——我的选择，你猜到了，playful（俏皮的）。mission 部分搞定。然后是 tech stack 的问题。我们的工程师习惯后端用 TypeScript，那就把这项加进需求。接着是 roadmap，它问要多细粒度——我选第一个。初步访谈结束，选 submit answers（提交答案）。

Agent 会请求写权限，这让改动处在你的控制下。如果你能接受相应的安全权衡，可以选"本 session 内同意这条命令的所有调用"。这在课程后续里你会一直见到。完成后，specs 目录下会有三个文件：`mission.md`、`tech-stack.md`、`roadmap.md`。

Human-in-the-loop 时间，开始审。比如 mission 漏了目标受众——也合理，agent 哪知道你们公司的业务。不要直接手改，而要继续对话。为了所有产出物彼此一致，更好的做法是请 agent 去改；手改可能漏改相关文档。项目是快速 prototype，我们要用 SQLite——我们没明说，但 agent 在 recommendations 里看出来了，它自然进了 tech stack。再做最后一次审。前期把事情都做对很重要。把 Constitution 提交上去，让它成为项目的活文档。AgentClinic 现在有了 constitution：mission、roadmap、tech stack，指引我们的项目。可以上手第一个特性了。

---

## L5 · Feature specification（特性规范）

### English

We now have a roadmap with features. But how do we build them? With specs of course, starting with the first roadmap feature. Let's take a look at the AgentClinic roadmap. Here's our phase one feature, Hello Hono. But it's too early to start coding. We need to discuss the spec and clarify all the details. A good plan outlines the approach, the sequence of work, and how to validate success.

Let's start with fresh agent context. The agent can get what it needs from the official source, the constitution. We will work on this feature in a separate branch, which we will indicate to the agent. Our prompt will help start a conversation with the agent about the feature spec, a plan for tasks, collect requirements, and a scorecard for validation.

The agent will ask you to make key decisions. Pay attention to potential conflicts or problems. You don't have to agree with the solutions proposed by the agent. Make sure to clarify anything that bothers you. First, regarding phase one scope, we'll keep it exactly as written. For the requirements, let's pin the Hono version and enforce strict TypeScript. For the confirmation, manual curl looks good. Then, let's submit. That was fast. Must have been a nano. Work is done and the ball is back on our side for reviewing the feature spec created by the agent.

Let's review the feature plan first. It's important to get these docs right in the beginning to keep the agent on track. If you find something wrong, ask the agent to fix it, to make sure it keeps the requirements and validation in sync. For example, we would like a nice looking placeholder home page in this first feature spec. We make the change via the agent.

Next, the requirements. These were also updated to reflect the homepage update. This is the right place to indicate any important technical needs or constraints. Don't speed through this, but don't state minor technical details like variable names here. We want to control the process but not oversteer the agent.

Last, the validation. Make sure the agent can check that it got it right. Again, these were updated for the homepage. We're done with the feature spec. Let's create a commit in the IDE.

Nice. We formulated a feature spec strategy: made a feature branch, interviewed with the agent about what the feature should do, put the results in markdown documents, reviewed these spec files, committed. The changes you make here in the specs will expand downstream into hundreds of lines of code. So time spent here is well spent. It's time for implementation.

### 中文

我们现在有了带特性的路线图。但怎么真的把它们建出来？当然是靠 spec，从路线图的第一个特性开始。先看 AgentClinic 的 roadmap。这是第一阶段的特性——Hello Hono。但现在就写代码还太早，我们得讨论 spec，把所有细节都澄清。一份好的计划要写清：思路、工作顺序、以及如何验证成功。

我们从一个干净的 agent context 开始。Agent 可以从官方来源——constitution——拿到它需要的东西。这个特性我们在一个单独的 branch 上做，我们会在 prompt 里告诉 agent。我们的 prompt 会引导 agent 和我们一起讨论 feature spec、任务计划、收集需求、并定一个用于验证的评分卡。

Agent 会让你做一些关键决策。盯紧潜在的冲突与问题。你不必同意 agent 提出的方案，任何让你别扭的地方都要澄清。第一，关于第一阶段的 scope，我们保持原样不动。需求方面，把 Hono 版本钉死，并强制 strict TypeScript。确认（confirmation）方式用手动 curl 即可。然后提交。很快——肯定是个 nano 模型。活干完了，球回到我们这边：审 agent 产出的 feature spec。

先审 feature plan。这些文档前期把它们做对非常关键，才能让 agent 一直在轨。有不对的就让 agent 改，让它保持需求与验证同步。比如我们希望第一份 feature spec 里有一个好看点的首页占位，那就让 agent 去改。

接着审 requirements。为了反映 homepage 的更新，它也被同步更新了。这里是写明重要技术需要或约束的地方。别走马观花，但也别在这儿写像变量名这种细节。我们要掌控过程，但不要"过度驾驶"agent。

最后审 validation。要能让 agent 检查自己做对没。它也因 homepage 的改动被同步更新了。feature spec 审完。在 IDE 里做一次 commit。

漂亮。我们敲定了一套 feature spec 策略：建 feature branch，和 agent 对话确认这个特性该做什么，把结果落到 markdown 文档里，审这些 spec 文件，commit。你在 spec 这边做的改动，下游会被放大成几百行代码。所以这里花的时间花得值。进入实现环节。

---

## L6 · Feature implementation（特性实现）

### English

We're working on the plan for the first feature in the project roadmap. We finished the feature spec. Let's do the full implementation of this feature. Just to refresh ourselves, we do a quick review of this feature spec's plan. This was the first feature, hello Hono. The feature spec documents have what we need.

To start your implementation, you'll want to clear out your context with a /clear command. Let's go back to the agent and enter a prompt to implement all the task groups. Sometimes you might choose to do task groups one at a time for even smaller steps and commits. This technique is especially helpful for areas where small mistakes can compound later, like in security or database management.

While Claude is running, you can observe the changes it displays in the console to see its progress in real time. Afterwards, we can watch for the final changes in the commit window and visit the changes. This gives us an early jump on reviewing the work. This is the key role of the developer in such a paradigm — to act as an architect or supervisor and ensure that the agent is provided with a clear contract.

We can also see the summary of what was done. As you can see, the agent provides extra details on the work performed for each of the task groups. Now, let's go to the package.json file. And in there, let's run the app. The server starts in the console. The browser shows the result of this feature spec. Not much. We said nano, but it's a good feeling to see pixels on the screen. We just implemented a feature. The agent did its validation. In the next lesson, we'll do ours.

### 中文

我们正在做项目路线图的第一个特性。feature spec 写完了，接下来做完整实现。快速回顾一下这份 feature spec 的 plan——第一个特性 hello Hono。feature spec 文档里有我们需要的一切。

开始实现前，用 `/clear` 命令清掉上下文。回到 agent，输入一个 prompt，让它把所有 task group 都实现。有时你可能会选择一次一个 task group，步子更小、commit 更细。这种技巧在"小错会复利叠加"的领域（比如安全、数据库管理）特别有用。

Claude 跑的时候你可以在 console 里看它展示的改动，实时跟进。完事后我们能在 commit 窗口看到最终改动并逐项检查——这让我们能早一步开始审阅工作。在这种范式里，开发者的核心角色就是这个：当建筑师或监工，确保 agent 拿到一份清晰的契约。

我们也能看它做了什么的总结。如你所见，agent 对每个 task group 都给出了执行细节。现在打开 `package.json`，在里面运行应用。服务端在 console 里启动，浏览器显示这份 feature spec 的结果——没什么内容。我们说了 nano，但看到屏幕上真有像素出来还是挺爽的。我们就这样实现了一个特性。Agent 做了它的验证，下节我们做我们的。

---

## L7 · Feature validation（特性验证）

### English

It's nice to have a feature ready, but don't merge yet. In this last feature step, we collaborate with the agent to review the work. Fortunately, programmers have good tools for this validation task. After all, programmers have long spent several hours per week on code review.

Start with the commit view. Let's go through the changes. Focus your review on high-level concerns like whether the features work and reflect the spec, rather than details like which CSS classes were implemented. For example, the Home.tsx is minimal. Nano really meant nano. We want a layout component with clear landmarks for header, main, and footer, but do these as sub-components. And create a CSS file. As it turns out, this mistake in the code flowed from a mistake in the plan. We didn't ask for this. Let's ask the agent to do the fix, thus correcting both spec and implementation.

This process that consists of the agent generating something and us verifying it is known as the human in the loop. Because agents are so fast at writing code, software developers have lately been talking about cognitive debt, the mental load of tracking what your code is doing and how it has evolved. That is why in order for this process to be fast and easy to control, and to reduce cognitive debt, the changes should be manageable.

The agent is making our changes, but also validating that these changes didn't break anything. Good news, the agent finished and gave us a quick summary. You can also see this in the editor. The feature plan was changed to add a Group 5 to list these new steps. The code now has a layout component. And a CSS file was added. It looks like the three subcomponents were just put in the same file, but that doesn't follow conventions. Let's put these in their own files using the IDE move tool.

These kinds of changes are easy in editors and we've always been good at this. No need for an agent, right? Let's just do it. Not so fast. This can lead to drift where other artifacts, specs, readmes, get out of sync. Let's ask the agent to fix any mentions and to make sure this doesn't waste some time later. All changes are done.

Did we break something? Tests would help, but the agent didn't install our testing package. Since this is needed for all features, we'll cover it in the next lesson. Now we're ready to mark this work as complete and merge our results.

Notice that the part of the constitution was updated alongside the feature in this branch. How to handle versioning of the specs and how to associate which specs created which code changes is an evolving topic in the community. The change to the roadmap is small, just checking off a step in the roadmap. So it's okay to keep these updates on the same branch. If the overall constitution update is more complicated, it might be better to do this in a separate branch.

Now we're ready to mark this work as complete and merge our results. We just use Spec-Driven Development to develop and validate our first feature. But wait, shouldn't we take another look at the project's broader scope?

### 中文

特性虽好，别急着 merge。在这一特性的最后一步，我们和 agent 一起审阅工作。好在程序员有一堆趁手的验证工具——毕竟我们多年来每周花好几个小时在 code review 上。

从 commit 视图开始，过一遍改动。审的时候聚焦在高层关切上——特性能不能跑、是否符合 spec——而不是抠"用了哪个 CSS 类"这种细节。举个例子，`Home.tsx` 极简：nano 是真 nano。我们想要的是一个带 header、main、footer 清晰"地标"的 layout 组件，并把这些拆成子组件；再建一个 CSS 文件。事实证明，这个代码上的失误其实是计划里的失误流下来的——我们没要求这些。让 agent 去修，这样 spec 和实现一起被纠正。

"agent 生成、人验证"的这个过程被称为 human in the loop（人在回路）。因为 agent 写代码太快，近来软件开发者都在谈 cognitive debt（认知债务）——追踪代码在做什么、演化到哪一步的脑力负担。这也是为什么，要想让这个过程又快又可控、并降低认知债务，改动就必须保持在可管理的大小。

Agent 做我们要的改动，同时也验证这些改动没搞坏东西。好消息，它完成了并给我们一个简短总结，编辑器里也能看到：feature plan 被改了，加了 Group 5 来列出这些新步骤；代码里现在有了一个 layout 组件；还多出一个 CSS 文件。不过三个子组件被塞到了同一个文件里，不符合惯例。我们用 IDE 的 move 工具把它们拆到各自独立的文件。

这种改动在编辑器里很简单，我们向来擅长，根本不需要 agent，对吧？直接动手。——慢着。这会导致漂移：其他产出物，比如 spec、readme 会和代码脱节。让 agent 去修正所有相关提法，避免以后浪费时间。所有改动完成。

有没有把东西弄坏？测试会有帮助，但 agent 还没装我们的测试包。因为所有特性都需要测试，我们会在下一节处理。现在可以把这项工作标记为完成并合并结果了。

注意这个 branch 里 constitution 的一部分跟着这个 feature 一起被更新了。spec 的版本管理怎么做、哪些 spec 产生了哪些代码改动如何关联——这些在社区里还是个演进中的话题。这次 roadmap 的改动很小，只是在 roadmap 上勾掉一步而已，所以和本 branch 放一起没关系。如果 constitution 的整体更新更复杂，可能放到单独 branch 里更合适。

现在可以把这项工作标记为完成并合并结果了。我们刚刚用 Spec-Driven Development 开发并验证了第一个特性。但等等，我们是不是该再回头看看项目的整体范围？

---

## L8 · Project replanning（项目再规划）

### English

We successfully implemented the first feature. Can't wait to do it again and again. Don't rush into it. Take a step back and reflect with some replanning. You have to run slow to run fast.

We have a workflow step called validation. Tests are good for validation, but we didn't give the agent all our testing preferences. Let's make a replanning branch to update the tech stack in our constitution. As you saw in the previous video, spec versioning is an evolving topic. The constitution is a living document. It's good practice to make updates to it in its own separate branch, so you can keep track of which versions of it produced which code.

We'll use a prompt to state our policy. It looks like the agent updated the package.json just to have a script, no dependency. Something to note for later, when we do run tests. This change to add a testing framework applies to future code. But let's tell the agent to update existing feature specs and implementation based on this constitution change.

After these two prompts, we have some good changes. The agent set up this testing change, but didn't write the tests themselves. Let's tell the agent to write some new tests. With this setup, we can run tests conveniently in our editor. Let's run under the debugger to give us a chance to step through code execution as part of the human in the loop. Looks good. We now have tests as part of validation. Let's commit.

Sometimes we realize we want to do something differently in the product plan. For example, after implementing the first feature, we got an update from the product manager. Because 40% of our users are on mobile, we want to emphasize a responsive design. Let's go to the agent. Tell the agent we want responsive design and to correct the product specs and feature specs as well as any code. Since we're so early on in development, this update is a small change, so it makes sense to directly implement it during replanning. But if the new work is big, it's better to schedule it on the roadmap as its own feature phase, instead of just doing it in replanning. Use your judgment.

Remember, we want the specs to capture decisions, not just the code. We want to try to keep both in sync to help team communication. The agent has implemented our responsive fix with updates not just in code, but also to the product and feature specs. Time for us to do our part and review the changes. Let's go through the diffs. This looks good. Let's do a commit. Remember, working in small steps with frequent commits keeps the review from overloading your brain.

While this first part of replanning is working, let's step back and do some project housekeeping. For example, let's revisit the project roadmap and look at the next task. Does it still make sense? The roadmap shows the next feature which we cover in the next lesson. Is it still the correct one? Looking through the roadmap, it looks like features two, three, four, and five kind of hang together. Let's update the roadmap to tackle those in one step. We'll commit and start on this new feature shortly.

The replanning step can be about a feature or the whole project. But replanning can also be about improving your spec-driven development workflow, across projects, across your organization. For example, maybe you have a few non-technical stakeholders that want to monitor the project's progress. You'd like it to update a changelog on each merge to main. Most AI coding agents support skills, a package of instructions and resources providing the agent new capabilities and expertise. Skills are great for definable, repeatable workflows that require context specific to your project or organization. A skill would be great for implementing a changelog update.

You could write this skill by hand, but many agents actually have skills that help you write a skill. Let's use the agent to help us write and maintain a changelog skill. The agent gets to work. One thing for us to consider is this skill unique to this project? Or should it be a standard part of all projects? This is a style choice and you'll gain better understanding as you use it.

You'll learn to automate your SDD workflow with skills. For example, your validation step might include updating the readme, linting, formatting, test writing, and other quality checks. You can work with your agent to package those into a validation skill. Repeatable process, less manual work.

The agent is finished. We can scroll through the window to see what it created and how to use it. Note that it chose to create this in the global skills area. This skill will now be usable across all projects. Also, Claude Code used the new skill to generate a CHANGELOG in this project. CHANGELOGs are how you talk to your stakeholders, including the agent. So, let's open it and review it manually. Looks good. We are finished with this replanning work. Let's wrap up with a commit. Then merge the branch.

Now that most of your work as a developer is in planning and validation, rather than implementing, make time between features to replan. In the next video, we'll work on another roadmap feature.

### 中文

第一个特性顺利上线，恨不得立刻再来一个、再来一个。别急。先退一步，做点 replanning（再规划）。要跑得快，就得先慢下来。

我们的工作流有一步叫 validation。测试是验证的好手段，但我们没把所有测试偏好都告诉 agent。我们开一个 replanning branch，在 constitution 里更新 tech stack。上一节也说了，spec 的版本管理还在演进；constitution 是活文档，最好在单独的 branch 里改它，这样才知道是"哪一版 constitution"产出了"哪一版代码"。

我们用一个 prompt 来声明这条政策。看起来 agent 只是在 package.json 里加了个 script，没加依赖。这点先记着，等我们真跑测试时要处理。这次"加入测试框架"的改动针对的是未来的代码；但我们顺便告诉 agent 根据这次 constitution 的变化去更新已有的 feature spec 和实现。

两轮 prompt 之后，改动看起来不错。Agent 把测试的骨架搭起来了，但没写测试本身。让 agent 去写几个新测试。有了这套配置，我们可以在编辑器里方便地跑测试。用 debugger 跑一遍，让我们作为 human in the loop 可以逐步跟踪执行。没问题。验证里现在有了测试。commit。

有时我们会意识到产品计划里有些东西想换个做法。比如第一个特性上线后，产品经理来了新需求——因为 40% 的用户在移动端，我们想强调响应式设计。去找 agent：告诉它我们要响应式设计，并请它同步修正 product spec、feature spec 以及相关代码。因为开发刚起步，这次更新是一个小改动，直接在 replanning 阶段实现即可。但要是新活儿很大，更好的做法是把它作为一个独立的 feature phase 排进 roadmap，而不是塞进 replanning。自己判断。

记住，我们希望 spec 捕捉的是**决策**，不只是代码；我们尽量让两者同步，以便团队沟通。Agent 把响应式修复做好了，改动不只落在代码上，还落在 product 和 feature spec 上。该我们上场审改动了。过 diff——看起来不错，commit。记住，**小步 + 频繁 commit** 才能让审阅不把你的大脑压垮。

replanning 的第一部分跑起来之后，我们退一步做些项目层面的整理。比如回头看 project roadmap，看下一个任务，还合理吗？roadmap 显示的是我们下一节会做的下一个特性——它还是当下应该做的那件事吗？翻一遍 roadmap，特性二、三、四、五彼此关系很紧，看起来是一块儿的。我们更新 roadmap，把它们作为一步来搞。commit，然后很快就开新特性。

Replanning 的范围可以是一个特性，也可以是整个项目。但它还可以是改进你的 spec-driven development 工作流本身——跨项目、跨组织。比如你可能有一些非技术的利益相关方想跟踪项目进展，你希望每次合并到 main 就自动更新 changelog。大多数 AI coding agent 都支持 skill——一个包含指令与资源、为 agent 带来新能力和专长的包。skill 很适合"可定义、可重复、需要你项目或组织专属上下文"的工作流。用 skill 来实现 changelog 的更新再合适不过。

你可以手写这个 skill，但其实很多 agent 有"帮你写 skill 的 skill"。让 agent 来帮我们编写并维护一个 changelog skill。Agent 开干。我们要思考的一件事是：这个 skill 只属于这个项目？还是应该作为所有项目的标准部件？这是风格选择，用多了你会有更好的判断。

你会学会用 skill 把 SDD 工作流自动化。比如 validation 这一步可能包含：更新 readme、lint、format、写测试以及其他质量检查——你可以和 agent 一起把这些打包成一个 validation skill。可重复的流程，更少的手工活。

Agent 完成了。滚动窗口看它创建了什么、怎么用。注意它把 skill 创建在全局 skill 区——这意味着它在所有项目里都能用。另外 Claude Code 用这个新 skill 在本项目里生成了 CHANGELOG。CHANGELOG 是你对利益相关方（包括 agent）讲话的方式，打开手工看一眼。没问题。这次 replanning 告一段落。commit 收尾，然后合并 branch。

既然作为开发者，你现在大多数工作都在 planning 和 validation，而非 implementation，特性之间就要留出时间做 replanning。下一节视频，我们继续做 roadmap 上的下一个特性。

---

## L9 · The second feature phase（第二个特性阶段）

### English

We have an improved constitution and an improved workflow. We're getting better. But before moving on to the next roadmap feature, let's tackle some strategies to deal with a common pain point. AI fatigue.

Agents can generate a lot of code with a lot of changes. This massive amount of code makes the human in the loop validation exhausting. So much to review. To fight this confusion, make sure you have a clean division between each feature phase. We should start each feature in the right flow state. Do I have unfinished work? Did I merge the last feature branch to main? Is the next roadmap item the right thing to do? Did I clear the agent's context to ensure the specs capture the intent instead of memory snapshots? And to let it focus its limited context budget on the next work. Take a moment to make a good start to help yourself finish.

We've done all this. Let's clear the context and start the next feature, "agents and ailments". As a note, we keep typing the same thing. We'll talk about how you can streamline this repeated prompting into a custom workflow in a future lesson. As before, the agent asks good questions. The first one is about whether to keep all these features in one phase. We already decided to do this together in the previous lesson, so we'll say yes. For the migrations, we'll choose plain SQL. For validation, let's select one, two, and three. That's good. Now, let's submit.

When the agent drafts the spec, we can see some of its choices. The agent's reasoning process is useful to watch. If you're using an agent with verbose mode, that could give you even more insight into its intermediate ideas. Looks good, but we want to make one small change to capture our intent. We want to use PicoCSS as our CSS framework.

Next, we review the three files in the feature spec, starting with the requirements. Let's ask the agent to change the requirements to make sure any other files reflect the change. When the work is finished, let's commit the feature spec. This time, we'll let the agent come up with the message.

With the spec for the second feature ready, let's go to implementation. The division between the planning phase and the feature phase helps us to not overflow context, hours and agents. If the feature seems too big to implement all at once, ask the agent to implement part of the feature plan first. This will keep the size of the changes manageable. Our agent has implemented the phase two feature. We watched the agent's progress, so we have a rough idea of what was done. But that's not enough. Slow down, do some thinking. Let's do an in-depth review of the changes.

Everybody has their own style on how much to review the agent's results. To play to the agent's strengths and reduce AI fatigue, stick to higher level requirements. Avoid nitpicking stuff like variable names. Just make sure it creates code that you can commit under your name. For example, it used inline type information for the props. We'd like the prop definition in a standalone TypeScript type. Let's tell the agent to fix this everywhere in the code. You probably didn't include this decision in your spec. Remember, an omission such as extracted prop types isn't a failure. You are evolving the spec as you discover new details and capturing that leads to better future results. We're finished with our review. This is a good moment for another small step commit.

Last step: validation. Let's review what the agent plans to do for the validation step in this feature spec. Looks good. Let's do our validation. Remember, we want to make sure the changes are good by running the application. But we also want to prevent cognitive debt by validating that we understand these changes. Tests are a great place to work through the flow. Let's read some tests and run them under the debugger as exploration.

Sometimes you need to validate that you weren't lied to. Tell the agent to spawn several sub-agents to do a deep review of the entire project with this feature change. This deep review gives the agent more space to think about the changes. And using sub-agents preserves the main agent's context window, rather than polluting it. These sub-agents will come back with a number of issues and recommendations. All these look good, so let's proceed. The agent goes off, does a bunch of fixes, runs tests, and gets our project into a good state. Keep this in your tool chest. The agent can usually find important issues during a second look.

Once we are finished with our validation, our feature is complete. It's time to commit our feature. As our last step on this feature, let's use the change log skill that we made in the previous lesson. We've now documented the changes on this feature branch. One more commit, then merge the branch.

In a previous lesson, we saw the replan step in between features. Let's do a quick version of this by looking at the roadmap. Does the next feature still seem right? Looks good. Our project is in good shape, ready for the next feature. This clean break between features helps manage AI fatigue. Now you can take a break for some coffee.

We're starting to see a repeatable SDD process for feature development. One that captures our decisions. In the next step, let's test that hypothesis. Do we have enough to build an MVP?

### 中文

我们有了更好的 constitution 和更好的工作流，越来越熟练。但在继续下一个 roadmap 特性之前，先处理一个常见痛点：AI 疲劳。

Agent 能生成很多代码、很多改动，这海量代码让 human-in-the-loop 的验证变得很累——要审的东西太多。要抵抗这种混乱，你必须在每个 feature phase 之间划出干净的边界。每个特性都要在正确的心流状态中起步。问自己：我有没有未完成的活儿？我把上一个 feature branch 合回 main 了吗？roadmap 上的下一项现在还该做吗？我有没有清空 agent 的 context，以保证 spec 捕捉的是意图、而不是记忆的快照？也让它把有限的上下文预算用在下一份工作上。花点时间好好起步，才有力气把事干完。

这些我们都做好了。清空上下文，开始下一个特性——"agents and ailments"。顺带一提，我们一直在输入同样的话。以后的课里我们会讲怎么把这种重复 prompting 流程化成自定义工作流。和上次一样，agent 会问好问题。第一个问题：是否把这几个特性放在同一个 phase 里？——上一节我们已经决定这样做，答 yes。migration 选 plain SQL。validation 选第 1、2、3 项。好，提交。

Agent 起草 spec 的时候，我们能看到它的一些选择。观察 agent 的推理过程很有用。如果你的 agent 有 verbose 模式，还能看到它中间的想法。整体不错，但我们想做一处小调整来锁定意图——我们要把 PicoCSS 作为 CSS 框架。

接下来逐一审 feature spec 的三个文件，从 requirements 开始。请 agent 改 requirements，并确保其他文件同步反映这一改动。完成后 commit 这次 feature spec——这回让 agent 自己拟 commit message。

第二个特性的 spec 就绪，进入实现阶段。planning 阶段和 feature 阶段之间的切分帮我们不让上下文、时间和 agent 溢出。如果一个特性大到没法一次性实现，就让 agent 先实现 feature plan 的一部分——把改动规模控制住。我们的 agent 把二阶段的特性实现完了。我们一路在看它的进度，对做了什么有个大概印象。但这还不够，慢下来，想一想——我们对改动做一次深度审阅。

审 agent 结果的"力度"各有风格。为了扬 agent 所长、减少 AI 疲劳，盯住更高层的需求，别对变量名这种细节鸡蛋里挑骨头。只要它生成的代码是你愿意签上自己名字 commit 出去的就行。举个例子，它给 props 用了 inline type 信息，我们希望把 prop 定义提成独立的 TypeScript type——让 agent 在全代码范围内修正。这条决定你大概率没写进 spec。记住，"提取 prop type"这种遗漏不是失败——随着你发现新的细节把 spec 演化出来，把它们记录下来，未来的结果会更好。审完。这又是一个小步 commit 的好时机。

最后一步：validation。看 agent 在这份 feature spec 的 validation 步骤里打算做什么——不错。然后做我们自己的验证。记住，我们想通过运行应用来确保改动靠谱；但同时也要通过"理解这些改动"来防止认知债务。测试是"走一遍流程"的好地方。读几个测试，在 debugger 下跑，当作探索。

有时你需要验证自己没被骗。让 agent 派出多个子 agent 对整个项目（带着这次特性改动）做一次深度 review。这种深度 review 给 agent 更多空间去思考改动；用子 agent 还能保住主 agent 的上下文窗口，不把它污染。子 agent 会带回一堆问题和建议。都看起来合理，继续。Agent 出去做一批修复、跑测试，把项目带到一个好状态。把这招收进你的工具箱——agent 在"回头再看"的时候常常能发现重要问题。

validation 完成，特性就完成了。commit 这个特性。作为这个特性的最后一步，用上一节我们做的 changelog skill——现在这个 feature branch 的改动也有文档了。再 commit 一次，合并 branch。

前一节里我们讲了特性之间的 replan 步骤。快速做一次：看 roadmap，下一个特性还合适吗？——合适。项目状态良好，准备好下一个特性。这种"特性之间的干净切分"帮你管理 AI 疲劳。你现在可以休息一下喝杯咖啡了。

一个可重复的、能承载决策的 SDD 特性开发流程正浮现出来。下一步，我们来检验这个假设——我们手头的东西够不够造一个 MVP？

---

## L10 · The MVP（最小可行产品）

### English

We made a lot of progress. Two features implemented with our spec workflow. But management just asked, of course, for an MVP. Have we made enough progress to do an experiment?

Let's do a variation of our standard feature spec prompt. This time we'll tell it to implement the rest of the roadmap and give some guidance about existing feature specs. In general, you should only implement such a large chunk if you feel confident in the quality of your constitution and spec. The better the context you provide your agent, the more confident you can be in getting a result aligned with your intentions. And you should be sure that you can handle review and validation. But since we've taken this risk now, let's view the MVP as an extreme test of our constitution and completed feature specs. If we now get something different from what we wanted, it means we need to very responsibly carry out another replanning phase to eliminate whatever led the agent astray.

The agent asked me some questions. Again, these are good questions and recommendations. A lot of spec files start as a back and forth conversation with an agent. Finished with the interview and we selected "Submit Answers". Let's write the MVP specs to disk. It's always good to review the specs. You'll often notice incorrect assumptions the agent made to fill the gaps. Let's give a quick look at the plan, the requirements and the validation. As always, we like to work in small steps with frequent commits. Let's save the planning stages feature spec.

We are finished with planning. It's time for the agent to go do a big implementation step. The agent now does a lot of work. Once it's finished, it's showtime. Let's see the MVP. We'll go to our scripts to run the application. As you can see, each section now has much more driven by sample data queried from the database.

At this point, usually we would validate the code results, but this is an MVP. Let's ask the agent to validate the specs. As it turns out, this was useful. The agent showed places where the MVP found holes in our planning. We can share this evaluation with the stakeholders for their MVP review, then merge or archive the branch.

Our spec-driven workflow succeeded. After a constitution and two features, the MVP produced a useful demo, thanks to our guidance. Of course, not all software projects involve building an MVP. Many projects are so-called brownfield. They start from an existing code base. Next, let's see how to introduce this workflow to legacy projects.

### 中文

进展不小：用 spec 工作流做了两个特性。但管理层——一如既往——开口要 MVP 了。我们的进展够不够做一次实验？

我们把标准 feature spec prompt 做个变体：这次让它把 roadmap 的剩余部分都实现，并对已有的 feature spec 给出一些指导。一般而言，只有当你对 constitution 和 spec 的质量有信心时，才适合让它一次吃掉这么大一块。你提供给 agent 的上下文越好，你就越有信心拿到对齐你意图的结果；你还得确信自己 hold 得住 review 和 validation。但既然这次我们愿意担这个风险，就把这次 MVP 当作对 constitution 和已完成 feature spec 的一次极限测试。如果现在出来的东西和我们要的不一样，那就意味着我们得很负责任地再开一轮 replanning，把让 agent 走偏的那些因素都清掉。

Agent 问了我几个问题。一如既往，都是好问题和好建议。很多 spec 文件就是从和 agent 的一来一回中发源的。访谈结束，选"Submit Answers"。把 MVP spec 写到磁盘上。审 spec 总是值得的——你常会发现 agent 用来填空的那些"错误假设"。快速看一遍 plan、requirements 和 validation。一如既往，我们偏好小步 + 频繁 commit——先把 planning 阶段的 feature spec 存下来。

planning 结束，轮到 agent 做一次大的 implementation。Agent 现在干很多活儿。它做完之后，就是 showtime。看我们的 MVP——进到 scripts 里把应用跑起来。可以看到每个 section 现在都由从数据库查询出来的样例数据驱动，内容丰满了很多。

按惯例这时我们应该验证代码结果，但这是 MVP，我们让 agent 去验证 spec。事实证明有用——agent 指出了 MVP 暴露出的 planning 漏洞。我们可以把这份评估分享给利益相关方做 MVP review，然后合并或归档这个 branch。

我们的 spec-driven 工作流成功了。有 constitution、两个特性做底，MVP 借着我们的引导输出了一个有用的 demo。当然，不是所有软件项目都从头做 MVP——许多项目是 brownfield（棕地），起点是已有代码库。下一节，我们看看怎么把这套工作流引入 legacy 项目。

---

## L11 · Legacy support（已有项目的支持）

### English

People say spec-driven development and even AI are only good for greenfield projects. But SDD is also good for existing legacy projects. In this lesson, we'll introduce SDD to an existing project.

To make it easy, we'll start with a project you already know. The AgentClinic MVP. We'll make it a legacy by starting on main without the specs folder in a new Claude Code session in a different project directory. We have some project background in the README.md file with some open work listed in TODO.md. Your legacy project might have full product plans in issue trackers, spreadsheets, Word documents, etc.

Remember our workflow? We'll start with the constitution step. We'll use almost the same prompt from lesson four. This time, we'll tell it to look for roadmap items in existing artifacts. In this case, a to-do file. Remember, the agent will discover and in a sense reverse engineer the SDD artifacts from the existing code base. The constitution will help align future code changes made by the agent with what past devs have already created. You can always add more context if you have it. For example, you might have been dropped into this project in order to improve its efficiency while implementing highly requested features. The process is the same, but the conversation might be richer as the agent has more artifacts, code, commits, documents, etc.

You'll see a lot of tool calls here as the agent explores the code base. Let's look at the constitution for our legacy project. In the mission, we have an audience, the project idea, and some extra information about the project. The tech stack shows that Claude extracted the project file structure, framework versions, and the clarifications we were asked. In the roadmap, we can see the work is organized in phases, matching the to-do file.

Let's create a commit for the constitution files that we produced with a commit message. Remember that in SDD, specs are part of your versioning strategy. Our legacy project is now placed on an SDD foundation.

From now on, the workflow is exactly the same as we discussed in previous lessons. First, we grab the next feature on the roadmap and plan it in a conversation with the agent. We'll be doing phase one feedback form. Very good. We now have a branch and specs for this feature. Let's review the feature spec — the plan, the requirements, and the validation. Then we commit the feature spec.

After we have made any corrections by chatting with the agent, we proceed to implement the feature. Once the agent is finished, we do our validation to make sure the code is good and the feature works as expected. Our feature loop is done, we can commit. And then merge the branch to main.

Now it's time for replanning. Make sure you give time for this. Since you just added SDD into your project, you may find a lot of things to tune. We now have a legacy project rebased onto Spec-Driven Development without a bunch of manual work. This gives us a well-documented flow, working in steps, using engineering and staying in control. The spec is now the memory of the project, so it doesn't fade.

### 中文

外界常说 spec-driven development——甚至 AI 本身——只适合 greenfield 项目。其实 SDD 也很适合已有的 legacy 项目。这一节我们就把 SDD 引入一个已有项目。

为了简单起见，我们拿一个你已经熟悉的项目作为起点：AgentClinic 的 MVP。我们把它"legacy 化"——换一个项目目录，在一个新的 Claude Code session 里从 main 开始，且不带 specs 目录。我们在 README.md 里有一些项目背景，在 TODO.md 里列出了待办事项。你自己的 legacy 项目，产品规划可能躺在 issue tracker、电子表格、Word 文档等各种地方。

还记得我们的工作流吗？从 constitution 这一步开始。我们沿用第四节的 prompt——几乎不变。不同的是，这次告诉它从已有的 artifact 里找 roadmap 条目，在这里就是一份 TODO 文件。记住，agent 会从已有代码库里"发现"并某种程度上"逆向"出 SDD 的各类产出物。Constitution 会让未来 agent 的代码改动与过去开发者已经建出来的东西对齐。如果你有更多上下文，尽管加——比如你可能是被派到这个项目去在实现高需求特性的同时提升它的效率。流程是一样的，但因为 agent 有了更多产出物、代码、commit、文档可参考，对话会更丰富。

你会看到一堆工具调用，agent 正在摸代码库。看一下我们这个 legacy 项目的 constitution。mission 里已经有了受众、项目想法以及一些补充信息。tech stack 显示 Claude 提取出了项目文件结构、框架版本以及我们被问到的那些澄清事项。roadmap 里工作被组织成 phase，和 TODO 文件对得上。

给这些 constitution 文件做一次 commit，附上 commit message。记住，SDD 里 spec 是你版本管理策略的一部分。我们的 legacy 项目现在被置于 SDD 的地基之上。

从这里开始，工作流就和前几节里讲的一模一样了。先从 roadmap 抓下一个特性，和 agent 聊着做 plan。我们做的是第一阶段的 feedback form。好了，这个特性的 branch 和 spec 有了。审 feature spec——plan、requirements、validation。然后 commit 这份 feature spec。

在和 agent 聊着做完所有修正后，我们进入实现阶段。Agent 做完，我们做自己的 validation，确保代码没问题、特性如期工作。特性循环走完，commit。然后把 branch 合回 main。

现在该做 replanning 了。务必留出时间：因为你刚把 SDD 加进你的项目，会发现一堆可以调的东西。我们就这样把一个 legacy 项目迁到了 Spec-Driven Development 上，而且没有堆山的手工劳动。我们收获了一条记录完善的流程：分步推进、用工程学、保持掌控。spec 现在成了项目的记忆，它不会褪色。

---

## L12 · Build your own workflow（打造你自己的工作流）

### English

You've now mastered an SDD workflow. Want something faster and lightweight? In this lesson, we will automate things but doing it our way with a custom process.

We previously showed Agent Skills, an open standard to give agents new capabilities and expertise. For example, we repeat the same prompt when starting a feature spec. Do this, do that, write these three files. Let's automate this with a skill and with help from the agent to write it. Ask the agent to use its skill creator to talk through this with us. As a note, there are many of these skill skills in the community that you can also install and use.

As the agent runs, it might ask some follow-up questions. These are usually quite good. When the interview is done, we submit the responses and the agent proceeds. While the agent is working on the skill, keep an eye on the output. Is it making the choices you wanted? Success. As you can see, the agent wrote the skill to this directory. As a note, skills can be per project or global.

Skills can be invoked in several ways. In your prompt, refer to the skill before saying what to do. Also, you can ask the agent to call a skill from another skill. According to the skills open standard, agents use the skill description to decide when to call it in a process called progressive disclosure. But their judgment isn't always perfect, especially as the context window gets larger. Use the same heuristic as file tagging: if you know you want a skill used, name it. That saves you some thinking tokens. Agents have built-in /commands like /clear. Though initially popular, many agents are moving from custom /commands over to skills.

Sometimes you need to give the agent more resources like access to some API, a private knowledge base, database, and so on. Until now the universal way to extend an agent has been MCP, Model Context Protocol. For example, agents need current quality context about packages. The most popular choice, Context7, an MCP that brings updated documentation of packages into your agent context. Now your agent can stay up to date with React 9.2 and higher instead of React 9.0.

MCP servers are still popular, but skills that use code tools like a CLI (command line interface) often accomplish the same purpose more elegantly. Context7 now suggests this, a skill that calls a CLI tool for Context7. Let's install the Context7 package for Claude Code. During the setup, we see this immediately: a choice between MCP server and CLI + Skills. We'll use the second choice. If this is your first time, you'll need to make an account. We already did so and logged in. Once done, we can go back into Claude Code and put it to use with an example prompt that uses Context7. As the agent runs, we see it detects the need to use Context7. When it completes, the agent shows it now knows how to find out information about our tech stack.

This trend from MCP servers to skills plus CLI is accelerating. People are rethinking MCP because CLI tools can take action with less setup and less context usage. As you scale your workflow implementation, skills, etc., you will want to share it with yourself, across machines, with teammates, perhaps with the outside world. Some agents such as Claude Code have plugins, a collection of agent extensions that can be installed and updated. There's a growing community of free plugins. Check them out to see if any will boost your SDD productivity. Remember, plugins are not yet a cross-agent standard. Like apps or dependencies, plugins can execute code, so make sure you trust them on install and update.

GitHub's Spec Kit is one attempt at formalizing a spec-driven development workflow with agents. Installing Spec Kit for a project gives you access to /commands in your agent, similar to the workflow you used in this course: spec-kit constitution, plan, tasks, and implement. Another popular alternative is OpenSpec from Fission AI. OpenSpec follows a similar propose, explore, apply, archive workflow, where propose and explore match with the plan step, apply matches with implement, and archive matches with replanning. It also has canonical patterns for quick features. Both packages include helpful features like branch management, verification scripts, and opinionated spec document formats. I encourage you to experiment with these open source workflows to help refine your own.

Sometimes in the middle of a feature, you have an idea. You want to research it with the agent, but you don't want to stop your branch work. For example, a choice of databases. But you're not yet committed to this idea, so you don't want it on the roadmap. The conversation produces some good ideas and some good questions. We accept most of the recommendations, but change our mind on one of them. You don't want to lose it. So, let's keep a backlog of research by telling the agent to write a report in a well-known location. This file is a record of your conversation and results. You can later ask the agent to schedule this research on the roadmap with a link to the backlog file. As this grows, you can write a skill to automate your research.

Spec-Driven Development helps the agent write code your way. You can adopt an existing SDD framework or tool, then customize it using skills to operate your projects with your team, your way.

### 中文

你已经掌握了一套 SDD 工作流。想要更快、更轻量的版本？这节课我们把事情自动化——但用自己的方式，一个定制化的流程。

前面我们介绍过 Agent Skill——一个给 agent 赋予新能力和专长的开放标准。比如每次开一个 feature spec 时，我们都在重复同一段 prompt："做这个、做那个、写这三个文件"。我们用一个 skill 把它自动化，并让 agent 帮我们写这个 skill。让 agent 调用它的 skill creator 和我们一起讨论。顺带一提，社区里已经有很多这样的"写 skill 的 skill"，你也可以直接装来用。

Agent 运转时可能会问一些后续问题，通常都挺到位。访谈结束，提交回答，agent 继续。它在写 skill 的时候你要盯着输出——它做的选择是你想要的吗？成功了。如你所见，agent 把 skill 写到了这个目录里。顺带一提，skill 可以是"每项目一份"，也可以是全局的。

skill 有好几种触发方式。最常见的：在你的 prompt 里先点名 skill 再说要做的事。你也可以让 agent 在一个 skill 里调用另一个 skill。根据 skill 开放标准，agent 用 skill 描述来判断"什么时候调用它"——这叫 progressive disclosure（渐进式披露）。但它的判断并不总是完美，尤其当 context window 变大时。参考"文件打 tag"的那套直觉：你明确知道想用某个 skill，就点名叫它——省下一些推理 token。agent 有内建的 `/command`（比如 `/clear`），当年很火；但现在很多 agent 正在从自定义 `/command` 迁移到 skill。

有时你需要给 agent 更多资源——访问某个 API、某个私有知识库、某个数据库等等。直到最近，扩展 agent 的通用方式是 MCP（Model Context Protocol）。比如 agent 需要关于依赖包的实时高质量上下文——最流行的选择是 Context7，一个把包的最新文档塞进你 agent 上下文的 MCP。这样 agent 就能跟上 React 9.2 乃至更高版本，而不是停留在 React 9.0。

MCP 服务器仍然流行，但使用 CLI（command line interface）工具的 skill，往往能用更优雅的方式达到同样的目的。Context7 现在就给出了这样一个建议：一个调用 Context7 CLI 工具的 skill。我们给 Claude Code 装上 Context7 这个包。在安装设置里，我们马上看到选择：MCP server 还是 CLI + Skills——选第二个。第一次用的话要建账号；我们已经建好并登录了。装完之后回到 Claude Code，用一条会用到 Context7 的示例 prompt 试一下。Agent 运行时，它识别出要用 Context7；完成后，它展示自己现在能查到我们技术栈相关的信息了。

这股"从 MCP server 迁移到 skill + CLI"的趋势在加速。人们在重新审视 MCP——因为 CLI 工具可以用更少的配置和更少的上下文消耗完成动作。随着你把工作流、skill 等等做得越做越大，你会想把它们分享——和自己的其他机器、和队友、甚至对外部世界。像 Claude Code 这样的 agent 提供 plugin：一组可安装、可更新的 agent 扩展。免费的 plugin 社区正在增长——去看看有没有能提升你 SDD 生产力的。记住，plugin 还不是跨 agent 的标准。和 app、依赖一样，plugin 可以执行代码，安装和更新前务必确认你信任它们。

GitHub 的 Spec Kit 是一次把"用 agent 做 spec-driven development"形式化的尝试。在一个项目里装 Spec Kit，你的 agent 就能用一组 `/command`，和本课的工作流类似：`spec-kit constitution`、`plan`、`tasks`、`implement`。另一个受欢迎的选择是 Fission AI 的 OpenSpec——它走的是一套相似的 propose、explore、apply、archive 流程：propose 和 explore 对应 plan 步骤；apply 对应 implement；archive 对应 replanning。它还针对"快速特性"提供了规范化模板。两者都带上了一些有用的东西：branch 管理、验证脚本、以及对 spec 文档格式的意见。我鼓励你在这些开源工作流里实验，借此精炼出你自己的那套。

有时你正在做一个特性做到一半，冒出一个想法，你想和 agent 研究一下，但又不想停掉手头的 branch。比如讨论数据库选型——但这个想法还没定下来，你不想把它放进 roadmap。对话里会冒出些不错的想法和好问题。我们接受大部分建议，但对其中一个改变主意。你不想把这些东西弄丢。那就让 agent 把这段"研究"写成一份报告放到一个大家都知道的位置，作为 research backlog——这份文件记录了你的对话和结论。以后你可以让 agent 把这项研究排进 roadmap，并附上到 backlog 文件的链接。当这类研究越攒越多，你可以写一个 skill 把这套研究流程自动化。

Spec-Driven Development 让 agent 按你的方式写代码。你可以采用现成的 SDD 框架或工具，再用 skill 把它们定制化，让你的项目以你和团队的方式运转。

---

## L13 · Agent replaceability（智能体的可替换性）

### English

Spec-Driven Development moves the work from the how to the what and why. Since agents and models progress so fast, you don't want your workflow tied to just one choice. In this lesson, we see how standards let us switch agents while keeping our workflow and even our tools.

These agent standards help for this goal: MCP for external tools, AGENTS.md for rules, Agent Skills for capturing repeatable workflows with extra context, and ACP for connecting agents to clients.

For example, Codex is a leading AI agent from OpenAI. It runs in a desktop app and in editors as well as in a terminal. Let's see it in action. Here is our feature spec skill from lesson 12 copied into Codex, which stores them in a different path. It runs just fine once migrated. This lets you switch back and forth between agents in the same project, keeping your SDD workflow.

We can also use different editors with different agents using the Agent Client Protocol standard. ACP makes it easy to connect agents and editors. If your agent and client support ACP, you have a perfect match. To make this plug and play even easier, the ACP registry automates finding, installing, and connecting agents with their clients. The ACP Registry covers the whole life cycle, making it easier to mix and match.

For example, in JetBrains IDEs, the AI Chat window leads to installation from the ACP Registry. The IDE can then show a listing of compatible agents. OpenCode is a popular open source agent. The ACP registry makes it easy to add to our IDE. Clicking install automates both the installation of OpenCode itself if needed, which is nice, and integration into the IDE. Once finished, your IDE now has native integration with a new agent. You can use this new agent alongside the other agents in your same editor as part of SDD.

These new agent standards are bringing new possibilities, but how do they actually work? The ACP architecture was designed to ease plugging together agents and clients. In fact, the protocol matches what's used in LSP. ACP is more than you think. For example, it covers Next Edit Suggestion in the editor and plan mode. To go one step further with ACP, you can write your own custom agent, install it locally in your tool.

Which agent to choose? The industry changes fast and benchmark sites have sprung up with leaderboards providing different evaluations. Of course, these leaderboards change fast. So make sure to keep up to date and base your decision on the criteria that matter to you.

Our specs work at a higher level, not tied to any one agent or IDE. In this lesson, we prove that. We built our workflow and tools to be independent of the agent. As more standards emerge, this flexibility should grow.

### 中文

Spec-Driven Development 把工作从"how"转移到了"what 和 why"。因为 agent 和模型进步太快，你不会想把自己的工作流绑死在某一个选择上。这节课我们看看，有哪些标准能让我们切换 agent，同时保住工作流乃至工具。

这些 agent 层的标准服务于这个目标：MCP 管外部工具，AGENTS.md 管规则，Agent Skills 捕捉"可重复的工作流 + 额外上下文"，ACP 把 agent 和客户端连起来。

举个例子：Codex 是 OpenAI 出的一款领先的 AI agent，它在桌面应用里、编辑器里、终端里都能跑。我们来看它工作：我们把第 12 节做的那个 feature spec skill 复制到 Codex（它把 skill 存在另一个路径里），迁过去后照样跑得起来。这意味着你可以在同一个项目里来回切换 agent，同时保住你的 SDD 工作流。

借助 Agent Client Protocol（ACP）标准，我们还可以把不同的编辑器和不同的 agent 组合起来。ACP 让连接 agent 和编辑器变得很容易。你的 agent 和客户端都支持 ACP，就是绝配。为了让这个"即插即用"更傻瓜，ACP registry 自动化了"找、装、连接 agent 与客户端"的过程——整个生命周期都覆盖了，混搭更简单。

举个例子，在 JetBrains IDE 里，AI Chat 窗口会把你引到从 ACP Registry 安装——然后 IDE 能列出所有兼容的 agent。OpenCode 是一款受欢迎的开源 agent，通过 ACP registry 很容易加到我们的 IDE 里。点"安装"，它会自动完成两件事：需要的话装 OpenCode 本身（这很贴心），以及把它集成进 IDE。完成后，你的 IDE 就对一个新 agent 有了原生集成。你可以把这个新 agent 和同一编辑器里的其他 agent 并用，作为 SDD 工作流的一部分。

这些新 agent 标准带来了新可能——但它们到底怎么工作？ACP 的架构是为了让"把 agent 和 client 接上"变轻松而设计的。事实上，这套协议和 LSP 用的那套是对齐的。ACP 覆盖的比你以为的更广——比如编辑器里的 Next Edit Suggestion、plan mode 都在它的范围内。再往前一步，你甚至可以自己写一个自定义 agent，把它本地安装到你的工具里。

那选哪个 agent？这个行业变得很快，各种 benchmark 网站冒出来做排行榜给出不同的评测。当然，这些排行榜本身也变得快。保持更新，按对你真正重要的标准来做决定。

我们的 spec 工作在更高层，不绑定任何 agent 或 IDE。这一节我们证明了这点——我们搭的工作流和工具，与 agent 无关。随着更多标准涌现，这种灵活性只会越来越强。

---

## 整理说明

- 字幕来源：`https://video.deeplearning.ai/JetBrains/C1/L{0..13}/subtitle/eng/sc-JetBrains-C1-L{N}-eng.vtt`（课程内嵌 VTT，直连可拉，无需 cookie）
- 课程共 17 项（DLAI 目录），其中 4 项为 reading/quiz（无视频），实际视频 14 节，对应 L0–L13
- 翻译策略：忠实原文，保留所有句子；按语义分段；术语保留英文（spec、constitution、roadmap、feature、agent、MCP、ACP、skill 等）以对齐社区用法
- Instructor: Paul Everitt (JetBrains) · Host: Andrew Ng (DeepLearning.AI)
- Last updated: 2026-04-21
