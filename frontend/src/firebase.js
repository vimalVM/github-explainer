// Import the functions you need from the SDKs you need
import { initializeApp } from "firebase/app";
import { getAuth } from "firebase/auth";

// Your web app's Firebase configuration
const firebaseConfig = {
  apiKey: "AIzaSyBUr7T5s_5nE4SCkqWsP4MvB4SV03-_aZ8",
  authDomain: "github-explainer.firebaseapp.com",
  projectId: "github-explainer",
  storageBucket: "github-explainer.firebasestorage.app",
  messagingSenderId: "309604329439",
  appId: "1:309604329439:web:87698ab16332797467066a"
};

// Initialize Firebase
const app = initializeApp(firebaseConfig);
export const auth = getAuth(app);
