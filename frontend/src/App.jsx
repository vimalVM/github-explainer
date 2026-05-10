import React, { useState, useEffect, useRef } from 'react';
import ReactMarkdown from 'react-markdown';
import { Search, Sparkles, Database, CheckCircle2, MessageSquare, Code, ArrowUp, Loader2, MoreVertical, Trash2, LogOut, User, Mail, Lock, Eye, EyeOff, Menu, X } from 'lucide-react';
import { auth } from './firebase';
import { createUserWithEmailAndPassword, signInWithEmailAndPassword, signOut, onAuthStateChanged, updateProfile } from 'firebase/auth';
import './index.css';

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000/api';

function App() {
  // Auth state
  const [user, setUser] = useState(null);
  const [authLoading, setAuthLoading] = useState(true);
  const [authMode, setAuthMode] = useState('login'); // 'login' or 'register'
  const [authEmail, setAuthEmail] = useState('');
  const [authPassword, setAuthPassword] = useState('');
  const [authName, setAuthName] = useState('');
  const [authError, setAuthError] = useState('');
  const [authSubmitting, setAuthSubmitting] = useState(false);
  const [showPassword, setShowPassword] = useState(false);

  // App state
  const [repos, setRepos] = useState({});
  const [activeRepo, setActiveRepo] = useState(null);
  const [repoInput, setRepoInput] = useState('');
  const [isIndexing, setIsIndexing] = useState(false);
  const [indexingRepos, setIndexingRepos] = useState({});
  const [chatHistory, setChatHistory] = useState({});
  const [chatInput, setChatInput] = useState('');
  const [isTyping, setIsTyping] = useState(false);
  const [dropdownOpen, setDropdownOpen] = useState(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const chatEndRef = useRef(null);

  // Listen for auth state changes
  useEffect(() => {
    const unsubscribe = onAuthStateChanged(auth, (firebaseUser) => {
      setUser(firebaseUser);
      setAuthLoading(false);
    });
    return () => unsubscribe();
  }, []);

  // Helper to get auth headers
  const getAuthHeaders = async () => {
    if (!user) return {};
    const token = await user.getIdToken();
    return { 'Authorization': `Bearer ${token}` };
  };

  useEffect(() => {
    if (user) fetchRepos();
  }, [user]);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [chatHistory, activeRepo]);

  // Close dropdown when clicking outside
  useEffect(() => {
    const closeDropdown = () => setDropdownOpen(null);
    document.addEventListener('click', closeDropdown);
    return () => document.removeEventListener('click', closeDropdown);
  }, []);

  useEffect(() => {
    const activePolling = Object.keys(indexingRepos);
    if (activePolling.length === 0) return;

    const interval = setInterval(async () => {
      const headers = await getAuthHeaders();
      for (const ns of activePolling) {
        try {
          const res = await fetch(`${API_URL}/index/${ns}/status`, { headers });
          const data = await res.json();
          if (data.status === 'completed' || data.status === 'already_indexed') {
            setIndexingRepos(prev => {
              const next = { ...prev };
              delete next[ns];
              return next;
            });
            fetchRepos();
          } else if (data.status === 'error') {
            setIndexingRepos(prev => {
              const next = { ...prev };
              delete next[ns];
              return next;
            });
            alert(`Failed to index repository: ${data.detail}`);
          }
        } catch (e) {
          // ignore network errors
        }
      }
    }, 3000);

    return () => clearInterval(interval);
  }, [indexingRepos]);

  // Auth handlers
  const handleLogin = async (e) => {
    e.preventDefault();
    setAuthError('');
    setAuthSubmitting(true);
    try {
      await signInWithEmailAndPassword(auth, authEmail, authPassword);
      setAuthEmail('');
      setAuthPassword('');
    } catch (err) {
      const msg = err.code?.replace('auth/', '').replace(/-/g, ' ') || err.message;
      setAuthError(msg.charAt(0).toUpperCase() + msg.slice(1));
    } finally {
      setAuthSubmitting(false);
    }
  };

  const handleRegister = async (e) => {
    e.preventDefault();
    setAuthError('');
    setAuthSubmitting(true);
    try {
      const credential = await createUserWithEmailAndPassword(auth, authEmail, authPassword);
      if (authName.trim()) {
        await updateProfile(credential.user, { displayName: authName.trim() });
      }
      setAuthEmail('');
      setAuthPassword('');
      setAuthName('');
    } catch (err) {
      const msg = err.code?.replace('auth/', '').replace(/-/g, ' ') || err.message;
      setAuthError(msg.charAt(0).toUpperCase() + msg.slice(1));
    } finally {
      setAuthSubmitting(false);
    }
  };

  const handleLogout = async () => {
    await signOut(auth);
    setRepos({});
    setActiveRepo(null);
    setChatHistory({});
  };

  const fetchRepos = async () => {
    try {
      const headers = await getAuthHeaders();
      const res = await fetch(`${API_URL}/repos`, { headers });
      const data = await res.json();
      setRepos(data.repos || {});
      
      // Initialize chat history for loaded repos
      const initialChat = {};
      Object.keys(data.repos || {}).forEach(ns => {
        initialChat[ns] = [];
      });
      setChatHistory(prev => ({ ...initialChat, ...prev }));
    } catch (err) {
      console.error("Failed to fetch repos", err);
    }
  };

  // When selecting a repo, fetch chat history from backend
  const handleSelectRepo = async (ns) => {
    setActiveRepo(ns);
    setSidebarOpen(false); // Close sidebar on mobile
    try {
      const headers = await getAuthHeaders();
      const res = await fetch(`${API_URL}/chat/${ns}/history`, { headers });
      const data = await res.json();
      if (data.messages && data.messages.length > 0) {
        setChatHistory(prev => ({ ...prev, [ns]: data.messages }));
      }
    } catch (err) {
      console.error("Failed to fetch chat history", err);
    }
  };

  const handleIndexRepo = async (e) => {
    e.preventDefault();
    if (!repoInput.startsWith('https://github.com/')) {
      alert('Must be a valid GitHub URL');
      return;
    }

    setIsIndexing(true);
    try {
      const headers = await getAuthHeaders();
      const res = await fetch(`${API_URL}/index`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...headers },
        body: JSON.stringify({ url: repoInput })
      });
      
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail);
      
      if (data.status === 'indexing') {
        setIndexingRepos(prev => ({ ...prev, [data.namespace]: data.url }));
        setRepoInput('');
        return;
      }
      
      setRepos(prev => ({ ...prev, [data.namespace]: data.url }));
      if (!chatHistory[data.namespace]) {
        setChatHistory(prev => ({ ...prev, [data.namespace]: [] }));
      }
      setActiveRepo(data.namespace);
      setRepoInput('');
    } catch (err) {
      alert("Failed to index: " + err.message);
    } finally {
      setIsIndexing(false);
    }
  };

  const handleDelete = async (ns) => {
    setDropdownOpen(null);
    if (!window.confirm("Are you sure you want to delete this repository?")) return;
    
    try {
      const headers = await getAuthHeaders();
      const res = await fetch(`${API_URL}/repos/${ns}`, { method: 'DELETE', headers });
      if (!res.ok) throw new Error("Failed to delete");
      
      setRepos(prev => {
        const next = { ...prev };
        delete next[ns];
        return next;
      });
      if (activeRepo === ns) setActiveRepo(null);
    } catch (err) {
      alert("Failed to delete: " + err.message);
    }
  };

  const handleSendMessage = async (e) => {
    e.preventDefault();
    if (!chatInput.trim() || !activeRepo || isTyping) return;

    const message = chatInput;
    setChatInput('');
    setIsTyping(true);

    // Add user message
    setChatHistory(prev => ({
      ...prev,
      [activeRepo]: [
        ...(prev[activeRepo] || []),
        { role: 'user', content: message }
      ]
    }));

    // Add empty assistant message to stream into
    setChatHistory(prev => ({
      ...prev,
      [activeRepo]: [
        ...(prev[activeRepo] || []),
        { role: 'assistant', content: '' }
      ]
    }));

    try {
      const headers = await getAuthHeaders();
      const res = await fetch(`${API_URL}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...headers },
        body: JSON.stringify({ namespace: activeRepo, question: message })
      });

      const reader = res.body.getReader();
      const decoder = new TextDecoder("utf-8");

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        
        const chunk = decoder.decode(value);
        const lines = chunk.split('\n');
        
        for (const line of lines) {
          if (line.startsWith('data: ')) {
            const dataStr = line.replace('data: ', '').trim();
            if (!dataStr) continue;
            
            try {
              const data = JSON.parse(dataStr);
              if (data.done) {
                break;
              }
              if (data.text) {
                setChatHistory(prev => {
                  const history = prev[activeRepo] || [];
                  const newHistory = [...history];
                  const lastMsg = { ...newHistory[newHistory.length - 1] };
                  lastMsg.content += data.text;
                  newHistory[newHistory.length - 1] = lastMsg;
                  return { ...prev, [activeRepo]: newHistory };
                });
              }
              if (data.error) {
                console.error(data.error);
              }
            } catch (err) {
              // parsing error
            }
          }
        }
      }
    } catch (err) {
      console.error(err);
    } finally {
      setIsTyping(false);
    }
  };

  // Loading screen
  if (authLoading) {
    return (
      <div className="auth-loading">
        <div className="spinner" style={{ width: 40, height: 40, borderWidth: 4 }}></div>
      </div>
    );
  }

  // Auth screen
  if (!user) {
    return (
      <div className="auth-container">
        <div className="auth-bg-glow"></div>
        <div className="auth-card">
          <div className="auth-brand">
            <div className="brand-title" style={{ justifyContent: 'center', fontSize: 32 }}>
              RepoBot <Sparkles size={28} color="#D4AF37"/>
            </div>
            <div className="brand-subtitle" style={{ textAlign: 'center', marginTop: 8 }}>
              AI-Powered Code Explorer
            </div>
          </div>

          <div className="auth-tabs">
            <button 
              className={`auth-tab ${authMode === 'login' ? 'active' : ''}`}
              onClick={() => { setAuthMode('login'); setAuthError(''); }}
            >
              Sign In
            </button>
            <button 
              className={`auth-tab ${authMode === 'register' ? 'active' : ''}`}
              onClick={() => { setAuthMode('register'); setAuthError(''); }}
            >
              Create Account
            </button>
          </div>

          <form onSubmit={authMode === 'login' ? handleLogin : handleRegister} className="auth-form">
            {authMode === 'register' && (
              <div className="auth-field">
                <User size={18} className="auth-field-icon" />
                <input
                  type="text"
                  placeholder="Full Name"
                  value={authName}
                  onChange={e => setAuthName(e.target.value)}
                  className="auth-input"
                />
              </div>
            )}
            <div className="auth-field">
              <Mail size={18} className="auth-field-icon" />
              <input
                type="email"
                placeholder="Email address"
                value={authEmail}
                onChange={e => setAuthEmail(e.target.value)}
                className="auth-input"
                required
              />
            </div>
            <div className="auth-field">
              <Lock size={18} className="auth-field-icon" />
              <input
                type={showPassword ? 'text' : 'password'}
                placeholder="Password"
                value={authPassword}
                onChange={e => setAuthPassword(e.target.value)}
                className="auth-input"
                required
                minLength={6}
              />
              <button 
                type="button" 
                className="auth-eye-btn"
                onClick={() => setShowPassword(!showPassword)}
              >
                {showPassword ? <EyeOff size={18} /> : <Eye size={18} />}
              </button>
            </div>

            {authError && <div className="auth-error">{authError}</div>}

            <button type="submit" className="btn-primary auth-submit" disabled={authSubmitting}>
              {authSubmitting ? (
                <div className="spinner" style={{ width: 20, height: 20 }}></div>
              ) : (
                authMode === 'login' ? 'Sign In' : 'Create Account'
              )}
            </button>
          </form>

          <div className="auth-footer">
            {authMode === 'login' ? (
              <span>Don't have an account? <button className="auth-link" onClick={() => { setAuthMode('register'); setAuthError(''); }}>Sign up</button></span>
            ) : (
              <span>Already have an account? <button className="auth-link" onClick={() => { setAuthMode('login'); setAuthError(''); }}>Sign in</button></span>
            )}
          </div>
        </div>
      </div>
    );
  }

  // Main app (authenticated)
  return (
    <div className="app-container">
      {/* Mobile hamburger */}
      <button className="mobile-menu-btn" onClick={() => setSidebarOpen(!sidebarOpen)}>
        {sidebarOpen ? <X size={24} /> : <Menu size={24} />}
      </button>

      {/* Sidebar overlay for mobile */}
      {sidebarOpen && <div className="sidebar-overlay" onClick={() => setSidebarOpen(false)} />}

      {/* Sidebar */}
      <div className={`sidebar ${sidebarOpen ? 'open' : ''}`}>
        <div className="brand">
          <div className="brand-title">RepoBot <Sparkles size={20} color="#D4AF37"/></div>
          <div className="brand-subtitle">AI-Powered Code Explorer</div>
        </div>

        {/* User info */}
        <div className="user-info">
          <div className="user-avatar">
            {(user.displayName || user.email || '?')[0].toUpperCase()}
          </div>
          <div className="user-details">
            <div className="user-name">{user.displayName || 'User'}</div>
            <div className="user-email">{user.email}</div>
          </div>
          <button className="btn-logout" onClick={handleLogout} title="Sign out">
            <LogOut size={18} />
          </button>
        </div>

        <div className="sidebar-section">
          <div className="sidebar-heading">Load a repository</div>
          <form onSubmit={handleIndexRepo}>
            <input 
              type="text" 
              className="input-field" 
              placeholder="https://github.com/user/repo"
              value={repoInput}
              onChange={e => setRepoInput(e.target.value)}
              disabled={isIndexing}
            />
            <button type="submit" className="btn-primary" disabled={isIndexing || !repoInput}>
              {isIndexing ? <div className="spinner"></div> : 'Index repository'}
            </button>
          </form>
        </div>

        {(Object.keys(repos).length > 0 || Object.keys(indexingRepos).length > 0) && (
          <div className="sidebar-section" style={{ flexGrow: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
            <div className="sidebar-heading">Loaded repositories</div>
            <div className="repo-list">
              {Object.entries(indexingRepos).map(([ns, url]) => {
                const repoName = url.replace('https://github.com/', '');
                return (
                  <div key={`idx-${ns}`} className="repo-card disabled" style={{ opacity: 0.6, cursor: 'not-allowed' }}>
                    <div className="repo-card-icon">
                      <Loader2 size={16} className="spinner" />
                    </div>
                    <div className="repo-card-name" title={repoName}>{repoName} (Indexing...)</div>
                  </div>
                );
              })}
              {Object.entries(repos).map(([ns, url]) => {
                const repoName = url.replace('https://github.com/', '');
                const isActive = ns === activeRepo;
                return (
                  <div 
                    key={ns} 
                    className={`repo-card ${isActive ? 'active' : ''}`}
                    onClick={() => handleSelectRepo(ns)}
                  >
                    <div className="repo-card-icon">
                      {isActive ? <CheckCircle2 size={16} color="#D4AF37" /> : <Database size={16} />}
                    </div>
                    <div className="repo-card-name" title={repoName}>{repoName}</div>
                    
                    <div 
                      className="repo-actions" 
                      onClick={(e) => { 
                        e.stopPropagation(); 
                        setDropdownOpen(dropdownOpen === ns ? null : ns); 
                      }}
                    >
                      <MoreVertical size={16} />
                      {dropdownOpen === ns && (
                        <div className="repo-dropdown">
                          <div 
                            className="repo-dropdown-item" 
                            onClick={(e) => { 
                              e.stopPropagation(); 
                              handleDelete(ns); 
                            }}
                          >
                            <Trash2 size={14} /> Delete
                          </div>
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>

      {/* Main Area */}
      <div className="main-area">
        {!activeRepo ? (
          <div className="empty-state">
            <Code size={64} className="empty-icon" />
            <div className="empty-title">Explore Any Repository</div>
            <div className="empty-subtitle">
              Paste a public GitHub repository URL in the sidebar and click Index repository to get started. Once indexed, you can ask anything about the code in plain English.
            </div>
            
            <div className="feature-grid">
              <div className="feature-card">
                <Search size={24} className="feature-icon" />
                <div className="feature-title">How does it work?</div>
                <div className="feature-desc">Clones the repo → splits code into chunks → embeds with HuggingFace → stores in Pinecone → answers via Groq LLM</div>
              </div>
              <div className="feature-card">
                <CheckCircle2 size={24} className="feature-icon" />
                <div className="feature-title">Completely free</div>
                <div className="feature-desc">Groq API free tier · Pinecone free tier · HuggingFace embeddings</div>
              </div>
              <div className="feature-card">
                <MessageSquare size={24} className="feature-icon" />
                <div className="feature-title">Tip</div>
                <div className="feature-desc">You can load multiple repos and switch between them in the sidebar.</div>
              </div>
            </div>
          </div>
        ) : (
          <>
            <div className="chat-header">
              <div className="chat-header-title">
                <Sparkles size={20} /> {repos[activeRepo]?.replace('https://github.com/', '')}
              </div>
              <div className="chat-header-subtitle">{repos[activeRepo]}</div>
            </div>
            
            <div className="chat-messages">
              {(chatHistory[activeRepo] || []).map((msg, idx) => (
                <div key={idx} className={`message ${msg.role}`}>
                  <div className={`avatar ${msg.role}`}>
                    {msg.role === 'user' ? <MessageSquare size={20} /> : <Sparkles size={20} />}
                  </div>
                  <div className="message-content">
                    {msg.role === 'assistant' && msg.content === '' ? (
                      <div className="spinner" style={{borderColor: 'rgba(255,255,255,0.1)', borderLeftColor: '#D4AF37'}}></div>
                    ) : (
                      <ReactMarkdown>{msg.content}</ReactMarkdown>
                    )}
                  </div>
                </div>
              ))}
              <div ref={chatEndRef} />
            </div>

            <div className="chat-input-container">
              <form className="chat-input-wrapper" onSubmit={handleSendMessage}>
                <input 
                  type="text" 
                  className="chat-input"
                  placeholder={`Ask anything about ${repos[activeRepo]?.replace('https://github.com/', '')}...`}
                  value={chatInput}
                  onChange={e => setChatInput(e.target.value)}
                  disabled={isTyping}
                />
                <button type="submit" className="btn-send" disabled={!chatInput.trim() || isTyping}>
                  <ArrowUp size={20} />
                </button>
              </form>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

export default App;
