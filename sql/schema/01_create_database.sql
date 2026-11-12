-- =============================================================================
-- 01_create_database.sql
-- Creates the project database. Safe to re-run (IF NOT EXISTS).
-- utf8mb4 stores every Unicode character (Portuguese accents, emoji in reviews).
-- =============================================================================
CREATE DATABASE IF NOT EXISTS marketplace_analytics
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_0900_ai_ci;
