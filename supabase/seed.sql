-- Local development seed. These credentials exist only in the local Supabase stack.
-- Dashboard login: admin@voice.test / dev-bbef31a0b328
INSERT INTO auth.users (
    instance_id, id, aud, role, email, encrypted_password, email_confirmed_at,
    raw_app_meta_data, raw_user_meta_data, created_at, updated_at,
    confirmation_token, recovery_token, email_change_token_new, email_change
) VALUES (
    '00000000-0000-0000-0000-000000000000', '11111111-1111-1111-1111-111111111111',
    'authenticated', 'authenticated', 'admin@voice.test', crypt('dev-bbef31a0b328', gen_salt('bf')), NOW(),
    '{"provider":"email","providers":["email"]}', '{}', NOW(), NOW(), '', '', '', ''
);

INSERT INTO auth.identities (
    id, user_id, provider_id, identity_data, provider, last_sign_in_at, created_at, updated_at
) VALUES (
    gen_random_uuid(), '11111111-1111-1111-1111-111111111111', '11111111-1111-1111-1111-111111111111',
    '{"sub":"11111111-1111-1111-1111-111111111111","email":"admin@voice.test","email_verified":true}',
    'email', NOW(), NOW(), NOW()
);

INSERT INTO contacts (phone, full_name, email, stage, deal_value, tags, last_contacted_at) VALUES
    ('+17135550101', 'Jane Doe', 'jane@example.com', 'new_lead', 1500, '{web-form}', NULL),
    ('+17135550102', 'Marcus Lee', 'marcus@example.com', 'no_answer_retry', 900, '{facebook}', NOW() - INTERVAL '3 hours'),
    ('+17135550103', 'Priya Shah', 'priya@example.com', 'follow_up_requested', 2400, '{referral}', NOW() - INTERVAL '1 day'),
    ('+17135550104', 'Tom Alvarez', NULL, 'not_interested', 0, '{}', NOW() - INTERVAL '2 days');
