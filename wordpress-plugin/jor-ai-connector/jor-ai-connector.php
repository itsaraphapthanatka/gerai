<?php
/**
 * Plugin Name: เจอ.AI Connector
 * Description: เชื่อมเว็บกับแพลตฟอร์ม เจอ.AI — inject schema JSON-LD ทั้งเว็บ, เสิร์ฟ /llms.txt, เปิดทาง AI crawler และรับคอนเทนต์จากแพลตฟอร์มผ่าน REST (API key)
 * Version: 0.2.2
 * Author: เจอ.AI
 * License: GPLv2 or later
 */

if (!defined('ABSPATH')) {
    exit;
}

define('JORAI_OPT', 'jorai_settings');
define('JORAI_VERSION', '0.2.2');

function jorai_opts() {
    return wp_parse_args(get_option(JORAI_OPT, array()), array(
        'api_key'    => '',
        'org_schema' => '',
        'llms_txt'   => '',
        'ai_bots'    => 1,
    ));
}

/* ---------- หน้า Settings ---------- */
add_action('admin_menu', function () {
    add_options_page('เจอ.AI Connector', 'เจอ.AI Connector', 'manage_options', 'jorai', 'jorai_settings_page');
});

add_action('admin_init', function () {
    register_setting('jorai_group', JORAI_OPT, 'jorai_sanitize');
});

function jorai_sanitize($in) {
    return array(
        'api_key'    => sanitize_text_field(isset($in['api_key']) ? $in['api_key'] : ''),
        'org_schema' => trim(isset($in['org_schema']) ? $in['org_schema'] : ''),
        'llms_txt'   => trim(isset($in['llms_txt']) ? $in['llms_txt'] : ''),
        'ai_bots'    => empty($in['ai_bots']) ? 0 : 1,
    );
}

function jorai_settings_page() {
    $o = jorai_opts();
    $suggest = empty($o['api_key']) ? wp_generate_password(32, false, false) : $o['api_key'];
    ?>
    <div class="wrap">
      <h1>เจอ.AI Connector</h1>
      <p>จับคู่เว็บนี้กับแพลตฟอร์ม เจอ.AI โดยตั้ง <strong>API Key เดียวกัน</strong>ทั้งสองฝั่ง</p>
      <p style="background:#f0f6fc;border-left:4px solid #2271b1;padding:8px 12px;margin:10px 0">
        💡 <strong>Schema องค์กร</strong> และ <strong>llms.txt</strong> ด้านล่าง แพลตฟอร์มจะเติมให้อัตโนมัติเมื่อเชื่อมต่อ — แก้เองได้ถ้าต้องการ
      </p>
      <form method="post" action="options.php">
        <?php settings_fields('jorai_group'); ?>
        <table class="form-table">
          <tr><th scope="row">API Key</th><td>
            <input type="text" name="<?php echo JORAI_OPT; ?>[api_key]" value="<?php echo esc_attr($o['api_key']); ?>" class="regular-text" placeholder="<?php echo esc_attr($suggest); ?>">
            <p class="description">คีย์ลับสำหรับให้แพลตฟอร์มส่งคอนเทนต์เข้ามา (เว้นว่าง = ปิดรับ) — แนะนำ: <code><?php echo esc_html($suggest); ?></code></p>
          </td></tr>
          <tr><th scope="row">Schema องค์กร (JSON-LD)</th><td>
            <textarea name="<?php echo JORAI_OPT; ?>[org_schema]" rows="6" class="large-text code"><?php echo esc_textarea($o['org_schema']); ?></textarea>
            <p class="description">ถูกใส่ใน &lt;head&gt; ทุกหน้า</p>
          </td></tr>
          <tr><th scope="row">llms.txt</th><td>
            <textarea name="<?php echo JORAI_OPT; ?>[llms_txt]" rows="6" class="large-text code"><?php echo esc_textarea($o['llms_txt']); ?></textarea>
            <p class="description">เสิร์ฟที่ <code><?php echo esc_html(home_url('/llms.txt')); ?></code></p>
          </td></tr>
          <tr><th scope="row">เปิดทาง AI crawler</th><td>
            <label><input type="checkbox" name="<?php echo JORAI_OPT; ?>[ai_bots]" value="1" <?php checked($o['ai_bots'], 1); ?>> เพิ่มกฎใน robots.txt ให้ GPTBot / ClaudeBot / PerplexityBot / Google-Extended ฯลฯ</label>
          </td></tr>
        </table>
        <?php submit_button(); ?>
      </form>
    </div>
    <?php
}

/* ---------- inject schema ใน <head> ---------- */
add_action('wp_head', function () {
    $o = jorai_opts();
    if (!empty($o['org_schema'])) {
        echo "\n<script type=\"application/ld+json\">" . $o['org_schema'] . "</script>\n";
    }
    if (is_singular()) {
        $s = get_post_meta(get_the_ID(), '_jorai_schema', true);
        if (!empty($s)) {
            echo "<script type=\"application/ld+json\">" . $s . "</script>\n";
        }
    }
}, 20);

/* ---------- robots.txt เปิดทาง AI bots ---------- */
add_filter('robots_txt', function ($output, $public) {
    $o = jorai_opts();
    if (empty($o['ai_bots'])) {
        return $output;
    }
    $bots = array('GPTBot', 'OAI-SearchBot', 'ChatGPT-User', 'ClaudeBot', 'PerplexityBot', 'Google-Extended', 'CCBot', 'Applebot-Extended');
    $extra = "\n# เจอ.AI Connector — allow AI crawlers\n";
    foreach ($bots as $b) {
        $extra .= "User-agent: " . $b . "\nAllow: /\n\n";
    }
    return $output . $extra;
}, 10, 2);

/* ---------- /llms.txt ----------
 * เสิร์ฟตั้งแต่ init (priority 0) ก่อน WP จะทำ canonical redirect /llms.txt -> /llms.txt/
 * (redirect นั้นทำให้ nginx ตอบ 403). รองรับทั้งมี/ไม่มี trailing slash. ไม่ใช้ rewrite rule.
 */
add_action('init', function () {
    $path = parse_url(isset($_SERVER['REQUEST_URI']) ? $_SERVER['REQUEST_URI'] : '', PHP_URL_PATH);
    if ($path === '/llms.txt' || $path === '/llms.txt/') {
        $o = jorai_opts();
        // cache ได้ (สำคัญ: ถ้า no-store nginx จะเก็บ 200 ใหม่ไม่ได้ → เสิร์ฟ 301 เก่าค้าง)
        header('Content-Type: text/plain; charset=utf-8');
        header('Cache-Control: public, max-age=300');
        echo ($o['llms_txt'] !== '') ? $o['llms_txt'] : ('# ' . get_bloginfo('name') . "\n");
        exit;
    }
}, 0);

/* ---------- REST API ให้แพลตฟอร์มเรียก ---------- */
add_action('rest_api_init', function () {
    register_rest_route('jor-ai/v1', '/ping', array(
        'methods'             => 'GET',
        'callback'            => 'jorai_ping',
        'permission_callback' => 'jorai_auth',
    ));
    register_rest_route('jor-ai/v1', '/publish', array(
        'methods'             => 'POST',
        'callback'            => 'jorai_publish',
        'permission_callback' => 'jorai_auth',
    ));
    register_rest_route('jor-ai/v1', '/settings', array(
        'methods'             => 'POST',
        'callback'            => 'jorai_set_settings',
        'permission_callback' => 'jorai_auth',
    ));
});

function jorai_auth($req) {
    $o = jorai_opts();
    if (empty($o['api_key'])) {
        return false;
    }
    $key = $req->get_header('X-JorAI-Key');
    return is_string($key) && hash_equals($o['api_key'], $key);
}

function jorai_ping($req) {
    return array('connected' => true, 'plugin' => 'jor-ai-connector', 'version' => JORAI_VERSION);
}

function jorai_set_settings($req) {
    // แพลตฟอร์ม push org schema / llms.txt / เปิด AI bots มาตั้งให้อัตโนมัติ (api_key คงเดิม)
    $p = $req->get_json_params();
    $o = jorai_opts();
    if (isset($p['org_schema'])) {
        $o['org_schema'] = trim(wp_unslash($p['org_schema']));
    }
    if (isset($p['llms_txt'])) {
        $o['llms_txt'] = trim(wp_unslash($p['llms_txt']));
    }
    if (isset($p['ai_bots'])) {
        $o['ai_bots'] = empty($p['ai_bots']) ? 0 : 1;
    }
    update_option(JORAI_OPT, $o);
    return array('ok' => true, 'updated' => array_keys((array) $p), 'version' => JORAI_VERSION);
}

function jorai_publish($req) {
    $p = $req->get_json_params();
    $status = (isset($p['status']) && in_array($p['status'], array('draft', 'publish'), true)) ? $p['status'] : 'draft';
    $postarr = array(
        'post_title'   => sanitize_text_field(isset($p['title']) ? $p['title'] : ''),
        'post_content' => isset($p['content_html']) ? $p['content_html'] : '',
        'post_status'  => $status,
        'post_type'    => 'post',
    );
    if (!empty($p['post_id'])) {
        $postarr['ID'] = intval($p['post_id']);
        $id = wp_update_post($postarr, true);
    } else {
        $id = wp_insert_post($postarr, true);
    }
    if (is_wp_error($id)) {
        return new WP_Error('jorai_publish_failed', $id->get_error_message(), array('status' => 500));
    }
    // schema JSON-LD เก็บใน meta แล้ว render ผ่าน wp_head (เลี่ยง kses ที่ตัด <script> ใน content)
    if (!empty($p['schema_json'])) {
        update_post_meta($id, '_jorai_schema', wp_unslash($p['schema_json']));
    }
    if (!empty($p['meta_title'])) {
        update_post_meta($id, 'rank_math_title', sanitize_text_field($p['meta_title']));
    }
    if (!empty($p['meta_desc'])) {
        update_post_meta($id, 'rank_math_description', sanitize_text_field($p['meta_desc']));
    }
    return array('ok' => true, 'id' => $id, 'link' => get_permalink($id), 'status' => $status);
}
