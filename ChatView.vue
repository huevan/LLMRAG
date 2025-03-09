<template>
    <div id="app">
        <div class="sidebar">
            <button class="custom-btn" @click="startNewSession">开启新会话</button>
            <button class="custom-btn" @click="goToKnowledgeBaseManagement">管理知识库</button>
            <div id="sessionList" class="session-list">
                <div
                    v-for="session in sessions"
                    :key="session.id"
                    class="session-item"
                    :class="{ 'active': String(session.id) === String(currentSessionId) }"
                    @click="loadSessionMsgs(session.id)"
                >
                    会话 {{ session.id }} - {{ formatDate(session.created_date) }}
                     <!-- 添加删除按钮 -->
                    <button class="delete-session-btn" @click="deleteSession(session.id)">删除</button>                  
                </div>
            </div>
        </div>
        <div class="chat-container">
            <div class="chat-header">
                <h1>RAG知识库搜索 + 智能对话</h1>
            </div>
            <div class="chat-messages" id="chatMessages" ref="chatMessages">
                <div
                    v-for="message in currentSession.messages"
                    :key="message.id"
                    class="message"
                    :class="{ 'user-message': message.type === 'question', 'bot-message': message.type === 'answer' }"
                >
                    <div class="avatar" :class="{ 'user-avatar': message.type === 'question', 'bot-avatar': message.type === 'answer' }">
                        <img :src="message.type === 'question' ? require('@/assets/user-avatar.png') : require('@/assets/bot-avatar.png')" alt="Avatar">
                        <span>{{ message.created_date }}</span>
                    </div>
                    <div class="message-content" :class="{ 'user-message-content': message.type === 'question', 'bot-message-content': message.type === 'answer' }">
                        <div class="think-content" v-html="renderMarkdown(message.think)"> </div>
                        <div class="content-text" v-html="renderMarkdown(message.final_content)">
                        </div>
                        <button
                            v-if="message.type === 'question'"
                            class="copy-button"
                            @click="copyToClipboard(message.final_content)"
                        >
                            复制
                        </button>
                    </div>
                </div>
            </div>
            <div class="chat-input">
                <input
                    type="text"
                    id="question-input"
                    placeholder="请输入你的问题"
                    v-model="question"
                    @keyup.enter="handleSubmit"
                />
                <button type="button" id="submit-button" @click="handleSubmit" class="custom-btn">
                    提交
                </button>
            </div>
        </div>
    </div>
  </template>
  
  <script>
  import { marked } from 'marked';
  import DOMPurify from 'dompurify';
  import { useRouter } from 'vue-router';
  
  // 配置 marked
  marked.setOptions({
      gfm: true,
      breaks: true,
      pedantic: false,
      smartLists: true,
      smartypants: false
  });
  
  // 配置 DOMPurify 允许数学相关字符
  DOMPurify.addHook('uponSanitizeAttribute', (node, data) => {
    if (data.attrName === 'class' && data.attrValue === 'math') {
      return true; // 允许保留 math 类
    }
  });
  
  DOMPurify.addHook('uponSanitizeElement', (node) => {
    if(node.tagName === 'MATH') return false; // 保留MathJax元素
  });
  
  DOMPurify.setConfig({
    ALLOWED_ATTR: ['class', 'style'], // 允许 class 和 style
    ALLOWED_TAGS: ['span', 'div', 'p', 'br', 'strong', 'em', 'code'] // 添加必要标签
  });
  
  export default {
    name: 'ChatView',
    data() {
        return {
            question: '',
            answer: '',
            isLoading: false,
            currentSessionId: null,
            sessions: [], // 用于存储会话历史
            mathJaxLoaded: false
        };
    },
  
    setup() {
      const router = useRouter();
  
      const goToKnowledgeBaseManagement = () => {
        router.push({ name: 'KnowledgeBaseManagement' });
      };
  
      return {
        goToKnowledgeBaseManagement
      };
    },
  
  
    computed: {
        currentSession() {
            return this.sessions.find((session) => session.id === this.currentSessionId) || {};
        }
    },
    methods: {
      async safeMathJaxTypeset() {
        //   if (!this.mathJaxLoaded ||!window.MathJax) return;
        //   try {
        //       await window.MathJax.typesetPromise();
        //   } catch (error) {
        //       console.error('MathJax 排版出错:', error);
        //   }
      },
  
      
        async loadSessions() {
            try {
                const response = await fetch('http://localhost:8000/sessions', {
                    method: 'GET'
                });
  
                if (!response.ok) {
                    throw new Error(`HTTP error! status: ${response.status}`);
                }
  
                const data = await response.json();
                // 直接对 data 进行排序
                this.sessions = data.sort((a, b) => {
                    const timestampA = new Date(a.created_date).getTime();
                    const timestampB = new Date(b.created_date).getTime();
                    return timestampB - timestampA;
                });
  
                if (this.sessions.length > 0) {
                    this.currentSessionId = this.sessions[0].id;
                    await this.loadSessionMsgs(this.sessions[0].id);
                }
            } catch (error) {
                console.error('加载会话历史出错:', error);
            }
        },
  
        // 其他方法保持不变
        async deleteSession(sessionId) {
          try {
            const response = await fetch(`http://localhost:8000/delete_session/${sessionId}`, {
              method: 'DELETE'
            });
  
            if (!response.ok) {
              throw new Error(`HTTP error! status: ${response.status}`);
            }
  
            // 从本地会话列表中移除已删除的会话
            this.sessions = this.sessions.filter(session => session.id !== sessionId);
  
            // 如果删除的是当前会话，重置当前会话 ID
            if (String(sessionId) === String(this.currentSessionId)) {
              if (this.sessions.length > 0) {
                this.currentSessionId = this.sessions[0].id;
                await this.loadSessionMsgs(this.sessions[0].id);
              } else {
                this.currentSessionId = null;
              }
            }
  
            alert('会话删除成功');
          } catch (error) {
            console.error('删除会话出错:', error);
            alert('删除会话失败，请稍后再试');
          }
        },
  
        async handleSubmit() {
            if (!this.question) return;
  
            if (!this.currentSessionId) {
                await this.startNewSession();
            }
  
            const currentSession = this.currentSession;
            const question = this.question;
  
            // 确保 currentSession 和 currentSession.messages 存在
            if (currentSession &&!currentSession.messages) {
                currentSession.messages = [];
            }
  
            if (currentSession && currentSession.messages) {
                // 推送用户问题消息
                currentSession.messages.push({
                    type: 'question',
                    final_content: question,
                    think: '',
                    id: Date.now()
                });
                this.scrollToBottom();
            }
  
            this.isLoading = true;
            this.answer = '';
  
            try {
                const response = await fetch(
                    `http://localhost:8000/ask?question=${encodeURIComponent(question)}&session_id=${this.currentSessionId}`,
                    {
                        method: 'GET',
                        headers: {
                            Accept: 'text/plain'
                        }
                    }
                );
  
                console.log('Response Status:', response.status);
                console.log('Response Headers:', response.headers);
                console.log('Response Type:', response.type);
  
                if (!response.ok) {
                    throw new Error(`HTTP error! status: ${response.status}`);
                }
  
                const reader = response.body.getReader();
                const decoder = new TextDecoder();
                let result = '';
  
                try {
                    let botMessageId = Date.now(); // 创建一个新的Bot消息ID
                    let thinkContent = ''; // 用于存储思考过程
                    let contentPart = ''; // 用于存储正式内容
  
                    if (currentSession && currentSession.messages) {
                        currentSession.messages.push({
                            type: 'answer',
                            final_content: '',
                            think: '',
                            id: botMessageId
                        });
                    }
  
                    for (;;) {
                        const { done, value } = await reader.read();
                        if (done) {
                            result += decoder.decode();
                            break;
                        }
                        const chunk = decoder.decode(value, { stream: true });
                        result += chunk;
  
                        // 动态拆分思考过程和正式内容
                        const parts = result.split(/<think>|<\/think>/);
                        if (parts.length >= 3) {
                            thinkContent = parts[1];
                            contentPart = parts[2];
                        } else if (parts.length === 2 && parts[0] === '') {
                            thinkContent = parts[1];
                            contentPart = '';
                        } else {
                            thinkContent = '';
                            contentPart = result;
                        }
  
                        // 更新Bot消息的内容
                        if (currentSession && currentSession.messages) {
                            const botMessageIndex = currentSession.messages.findIndex(
                                (msg) => msg.id === botMessageId
                            );
                            if (botMessageIndex !== -1) {
                                currentSession.messages[botMessageIndex].think = thinkContent;
                                currentSession.messages[botMessageIndex].final_content = contentPart;
                                this.scrollToBottom(); // 每次更新消息内容后滚动到最底部
                            }
                        }
                    }
                } finally {
                    reader.releaseLock();
                }
            } catch (error) {
                console.error('请求出错:', error);
                this.answer = '请求出错，请稍后再试。';
  
                if (currentSession && currentSession.messages) {
                    const botMessageId = Date.now(); // 创建一个新的Bot消息ID
                    currentSession.messages.push({
                        type: 'answer',
                        think: '',
                        final_content: '请求出错，请稍后再试。',
                        id: botMessageId
                    });
                    this.scrollToBottom(); // 出现错误时也滚动到最底部
                }
            } finally {
                this.isLoading = false;
                this.question = '';
                this.$nextTick(async () => {
                    await this.safeMathJaxTypeset();
                });
            }
        },
        renderMarkdown(rawText) {
          // 匹配 $...$ 和 $$...$$ 包裹的公式
          const latexBlocks = rawText.match(/\$\$.*?\$\$|\$.*?\$/g) || [];
          let processed = rawText;
          latexBlocks.forEach(formula => {
              // 保留原始LaTeX内容
              processed = processed.replace(formula, `<div class="math">${formula}</div>`);
          });
          
          const html = marked(processed);
          
          return DOMPurify.sanitize(html, { 
              ADD_TAGS: ['math'], 
              ADD_ATTR: ['xmlns'] 
          });
       },
        async startNewSession() {
            try {
                const response = await fetch('http://localhost:8000/new_session', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    }
                });
  
                if (!response.ok) {
                    throw new Error(`HTTP error! status: ${response.status}`);
                }
  
                const data = await response.json();
                const newSessionId = data.id;
                const now = new Date();
  
                this.currentSessionId = newSessionId;
                this.sessions.unshift({
                    id: newSessionId,
                    created_date: now,
                    messages: []
                });
                this.scrollToBottom();
            } catch (error) {
                console.error('创建新会话出错:', error);
            }
        },
        async loadSessionMsgs(sessionId) {
            this.currentSessionId = sessionId;
            try {
                const response = await fetch(`http://localhost:8000/session/${sessionId}/messages`, {
                    method: 'GET'
                });
  
                if (!response.ok) {
                    throw new Error(`HTTP error! status: ${response.status}`);
                }
  
                const messages = await response.json();
                const currentSession = this.sessions.find(session => session.id === sessionId);
                if (currentSession) {
                    currentSession.messages = messages;
                }
                this.scrollToBottom();
                this.$nextTick(async () => {
                    await this.safeMathJaxTypeset();
                });
            } catch (error) {
                console.error('加载会话消息出错:', error);
            }
        },
        copyToClipboard(text) {
            navigator.clipboard
              .writeText(text)
              .then(() => {
                    alert('已复制到剪贴板');
                })
              .catch((err) => {
                    console.error('复制失败:', err);
                    alert('复制失败，请重试。');
                });
        },
        scrollToBottom() {
          this.$nextTick(() => {
              const chatMessages = this.$refs.chatMessages;
              if (chatMessages) {
                  chatMessages.scrollTop = chatMessages.scrollHeight;
                  this.safeMathJaxTypeset(); // 滚动后触发公式渲染
              }
          });
        },
        formatDate(date) {
            const d = new Date(date);
            const year = d.getFullYear();
            const month = String(d.getMonth() + 1).padStart(2, '0');
            const day = String(d.getDate()).padStart(2, '0');
            return `${year}/${month}/${day}`;
        },
  
    },
    mounted() {
      // 动态加载 MathJax 脚本
    //   if (!this.mathJaxLoaded) {
    //       const script = document.createElement('script');
    //       script.src = 'https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js';
    //       script.async = true;
  
    //       // 添加加载完成回调
    //       script.onload = () => {
    //           this.mathJaxLoaded = true;
    //           window.MathJax = {
    //               tex: {
    //                   inlineMath: [['$', '$'], ['\\(', '\\)']]
    //               },
    //               startup: {
    //                   pageReady: () => {
    //                       return window.MathJax.startup.defaultPageReady();
    //                   }
    //               }
    //           };
    //           this.loadSessions(); // 确保在 MathJax 加载完成后加载会话
    //       };
  
    //       document.head.appendChild(script);
    //   }
        this.loadSessions(); // 确保在 MathJax 加载完成后加载会话
    }
  };
  </script>
  
  <style scoped>
  @import '@/assets/styles.css';
  
  </style>