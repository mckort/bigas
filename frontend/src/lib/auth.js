import { initializeApp } from 'firebase/app'
import {
  getAuth,
  signInWithEmailAndPassword,
  signInWithPopup,
  GoogleAuthProvider,
  onAuthStateChanged,
} from 'firebase/auth'
import { setToken } from './api'

let auth = null
let authMode = 'dev'

export async function initAuth() {
  const res = await fetch('/api/auth/config')
  const config = await res.json()
  authMode = config.auth_mode || 'dev'

  if (authMode === 'firebase' && config.firebase?.apiKey) {
    const app = initializeApp(config.firebase)
    auth = getAuth(app)
  }
  return { authMode, auth }
}

export function subscribeAuth(callback) {
  if (authMode !== 'firebase' || !auth) {
    const token = localStorage.getItem('bigas_chat_token')
    callback(token ? { email: 'dev@bigas.local' } : null)
    return () => {}
  }
  return onAuthStateChanged(auth, async (user) => {
    if (user) {
      const token = await user.getIdToken()
      setToken(token)
      callback(user)
    } else {
      setToken('')
      callback(null)
    }
  })
}

export async function loginEmail(email, password) {
  if (authMode === 'dev') {
    setToken(password || 'bigas-dev-token')
    return { email: email || 'dev@bigas.local' }
  }
  if (!auth) throw new Error('Firebase auth not configured')
  const cred = await signInWithEmailAndPassword(auth, email, password)
  const token = await cred.user.getIdToken()
  setToken(token)
  return cred.user
}

export async function loginGoogle() {
  if (authMode === 'dev') {
    setToken('bigas-dev-token')
    return { email: 'dev@bigas.local' }
  }
  if (!auth) throw new Error('Firebase auth not configured')
  const provider = new GoogleAuthProvider()
  const cred = await signInWithPopup(auth, provider)
  const token = await cred.user.getIdToken()
  setToken(token)
  return cred.user
}

export function logout() {
  setToken('')
  if (auth) auth.signOut()
}

export function isDevMode() {
  return authMode === 'dev'
}
