package dev.hitl.demo;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;
import org.springframework.dao.DataAccessException;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.jdbc.support.GeneratedKeyHolder;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class DemoController {
    private final JdbcTemplate jdbc;
    private final String instance = System.getenv().getOrDefault("HOSTNAME", "local");
    private static final RowMapper<StoredRecord> RECORD_MAPPER = (row, number) ->
        new StoredRecord(row.getLong("id"), row.getString("value"), row.getObject("created_at", OffsetDateTime.class));

    public DemoController(JdbcTemplate jdbc) { this.jdbc = jdbc; }

    @GetMapping("/health")
    public Map<String, String> health() {
        jdbc.queryForObject("SELECT 1", Integer.class);
        return Map.of("status", "UP", "database", "UP", "instance", instance);
    }

    @GetMapping("/api/instance")
    public Map<String, String> instance() { return Map.of("instance", instance); }

    @GetMapping("/api/records")
    public List<StoredRecord> records() {
        return jdbc.query("SELECT id, value, created_at FROM demo_records ORDER BY id", RECORD_MAPPER);
    }

    @PostMapping("/api/records")
    public ResponseEntity<StoredRecord> create(@Valid @RequestBody CreateRecord request) {
        var key = new GeneratedKeyHolder();
        jdbc.update(connection -> {
            var statement = connection.prepareStatement("INSERT INTO demo_records (value) VALUES (?)", new String[]{"id"});
            statement.setString(1, request.value());
            return statement;
        }, key);
        StoredRecord record = jdbc.queryForObject(
            "SELECT id, value, created_at FROM demo_records WHERE id = ?", RECORD_MAPPER, key.getKey());
        return ResponseEntity.status(HttpStatus.CREATED).body(record);
    }

    @ExceptionHandler(DataAccessException.class)
    public ResponseEntity<Map<String, String>> databaseUnavailable() {
        return ResponseEntity.status(HttpStatus.SERVICE_UNAVAILABLE)
            .body(Map.of("status", "DOWN", "database", "DOWN", "instance", instance));
    }

    public record CreateRecord(@NotBlank @Size(max = 500) String value) {}
    public record StoredRecord(long id, String value, OffsetDateTime createdAt) {}
}
