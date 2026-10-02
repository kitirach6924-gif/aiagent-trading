import { initializeApp, type FirebaseApp } from "firebase/app";
import {
  GoogleAuthProvider,
  signInWithPopup,
  getAuth,
  onAuthStateChanged,
  signOut,
  type User,
} from "firebase/auth";
import { getFirestore } from "firebase/firestore";

const firebaseConfig = {
  apiKey: import.meta.env.VITE_FIREBASE_API_KEY as string,
  authDomain: import.meta.env.VITE_FIREBASE_AUTH_DOMAIN as string,
  projectId: import.meta.env.VITE_FIREBASE_PROJECT_ID as string,
  storageBucket: import.meta.env.VITE_FIREBASE_STORAGE_BUCKET as string,
  messagingSenderId: import.meta.env.VITE_FIREBASE_MESSAGING_SENDER_ID as string,
  appId: import.meta.env.VITE_FIREBASE_APP_ID as string,
};

export const firebaseConfigured = Boolean(firebaseConfig.apiKey && firebaseConfig.projectId);

// Firestore database id (Spark tier: named DB "ai-mt5-default" — ไม่ใช่ "(default)")
const FIRESTORE_DB_ID = (import.meta.env.VITE_FIRESTORE_DB_ID as string | undefined) ?? "ai-mt5-default";

// Initialize only when configured, so the app still runs without Firebase (local dev).
let _app: FirebaseApp | null = null;
function app(): FirebaseApp {
  if (!_app) _app = initializeApp(firebaseConfig);
  return _app;
}

export const auth = firebaseConfigured ? getAuth(app()) : undefined;
export const firestore = firebaseConfigured ? getFirestore(app(), FIRESTORE_DB_ID) : undefined;

export async function loginGoogle(): Promise<void> {
  if (!auth) throw new Error("Firebase not configured — use Local mode");
  const provider = new GoogleAuthProvider();
  await signInWithPopup(auth, provider);
}

export async function loginEmail(email: string, password: string): Promise<void> {
  if (!auth) throw new Error("Firebase not configured — use Local mode");
  const { signInWithEmailAndPassword } = await import("firebase/auth");
  await signInWithEmailAndPassword(auth, email, password);
}

export async function logout(): Promise<void> {
  if (auth) await signOut(auth);
}

export function watchAuth(cb: (u: User | null) => void): () => void {
  if (!auth) {
    cb(null);
    return () => undefined;
  }
  return onAuthStateChanged(auth, cb);
}

export async function getIdToken(): Promise<string | null> {
  return auth && auth.currentUser ? await auth.currentUser.getIdToken() : null;
}
